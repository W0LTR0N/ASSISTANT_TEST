import socket
import asyncio
import audioop
import random
import re
import struct
import time
import json
from config import PLUSOFON_SIP_USER, PLUSOFON_SIP_PASSWORD, PLUSOFON_SIP_HOST, PUBLIC_IP
from core.logger import log_info, log_error
from core.stt_logic import transcribe_audio_yandex
from core.gpt_logic import ask_yandex_gpt, clear_session_context, get_session_history_formatted
from core.tts_logic import synthesize_speech_yandex
from core.albato_sender import send_lead_to_albato

async def generate_call_summary(transcript: str) -> dict:
    if not transcript:
        return {
            "summary": "Разговор не состоялся или был слишком коротким.",
            "client_name": "Не указано",
            "car_model": "Не указано",
            "service": "Не указано",
            "preferred_time": "Не указано"
        }
   
    system_instruction = "Ты — AI-аналитик. Твоя задача — извлечь данные из транскрипта и вернуть ИСКЛЮЧИТЕЛЬНО валидный JSON без разметки и без дополнительного текста."

    prompt = f"""
    Проанализируй транскрипт телефонного разговора детейлинг-центра и выдели данные строго в формате JSON без разметки:
    {{
      "summary": "краткое резюме разговора в 1-2 предложениях",
      "client_name": "имя клиента или 'Не указано'",
      "car_model": "марка и модель авто или 'Не указано'",
      "service": "запрашиваемая услуга или 'Не указано'",
      "preferred_time": "желаемая дата/время визита или 'Не указано'"
    }}

    Транскрипт:
    {transcript}
    """
    raw_res = await ask_yandex_gpt(prompt, session_id="summary_generator", system_override=system_instruction)
 
    try:
        # Очищаем от возможных Markdown тэгов
        clean_json = re.sub(r'```json|```', '', raw_res).strip()
        data = json.loads(clean_json)
        return data
    except Exception:
        return {
            "summary": raw_res,
            "client_name": "Не определено",
            "car_model": "Не определено",
            "service": "Не определено",
            "preferred_time": "Не определено"
        }

class RTPProtocol(asyncio.DatagramProtocol):
    def __init__(self, session_id):
        self.session_id = session_id
        self.transport = None
        self.pcm_buffer = bytearray()
        self.last_packet_time = time.time()
        self.is_processing = False
        self.sequence_number = random.randint(100, 10000)
        self.timestamp = random.randint(1000, 100000)
        self.remote_target = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) < 12:
            return

        if not self.remote_target:
            self.remote_target = addr

        payload = data[12:]
        try:
            pcm_frame = audioop.alaw2lin(payload, 2)
            self.pcm_buffer.extend(pcm_frame)
            self.last_packet_time = time.time()
        except Exception as e:
            log_error(f"Ошибка PCMA декодирования: {e}")

    async def send_audio_response(self, pcm_data):
        if not self.remote_target or not pcm_data:
            log_error("Нет remote_target для отправки RTP ответа")
            return

        alaw_data = audioop.lin2alaw(pcm_data, 2)
        frame_size = 160  # 20ms при 8000Hz
   
        for i in range(0, len(alaw_data), frame_size):
            chunk = alaw_data[i:i+frame_size]
            if len(chunk) < frame_size:
                chunk = chunk + b'\xd5' * (frame_size - len(chunk))

            header = struct.pack(
                "!BBHII",
                0x80,
                0x08,
                self.sequence_number & 0xFFFF,
                self.timestamp & 0xFFFFFFFF,
                0x12345678
            )
       
            self.sequence_number += 1
            self.timestamp += frame_size
       
            try:
                self.transport.sendto(header + chunk, self.remote_target)
            except Exception as e:
                log_error(f"Ошибка отправки RTP: {e}")

            await asyncio.sleep(0.018)

class SIPProtocol(asyncio.DatagramProtocol):
    def __init__(self, worker):
        self.worker = worker
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.worker.transport = transport
        log_info("SIP UDP Сокет успешно открыт.")

    def parse_sdp_remote_media(self, sdp_text):
        ip_match = re.search(r'c=IN IP4\s+([\d\.]+)', sdp_text, re.I)
        port_match = re.search(r'm=audio\s+(\d+)', sdp_text, re.I)
   
        ip = ip_match.group(1) if ip_match else None
        port = int(port_match.group(1)) if port_match else None
        return ip, port

    def datagram_received(self, data, addr):
        msg = data.decode('utf-8', errors='ignore')
   
        if "INVITE sip:" in msg:
            log_info(f"Входящий вызов от {addr}")
       
            call_id_m = re.search(r'Call-ID:\s*(.*)', msg, re.I)
            cseq_m = re.search(r'CSeq:\s*(.*)', msg, re.I)
            from_m = re.search(r'From:\s*(.*)', msg, re.I)
            to_m = re.search(r'To:\s*(.*)', msg, re.I)
            via_m = re.search(r'Via:\s*(.*)', msg, re.I)

            call_id = call_id_m.group(1).strip() if call_id_m else "123"
            cseq = cseq_m.group(1).strip() if cseq_m else "1 INVITE"
            from_hdr = from_m.group(1).strip() if from_m else ""
            to_hdr = to_m.group(1).strip() if to_m else ""
            via_hdr = via_m.group(1).strip() if via_m else f"Via: SIP/2.0/UDP {addr[0]}:{addr[1]}"

            if ";tag=" not in to_hdr.lower():
                to_hdr = f"{to_hdr};tag={random.randint(1000,9999)}"

            phone_m = re.search(r'sip:\+?(\d+)', from_hdr)
            phone = phone_m.group(1) if phone_m else "Неизвестный"

            remote_ip, remote_port = self.parse_sdp_remote_media(msg)
            if not remote_ip:
                remote_ip = addr[0]

            session_id = f"call_{random.randint(1000, 9999)}"
            rtp_port = random.randint(10000, 10040)

            asyncio.create_task(
                self.worker.start_rtp_session(session_id, rtp_port, phone, remote_ip, remote_port)
            )

            sdp_body = self.worker.generate_sdp(rtp_port)

            response = (
                f"SIP/2.0 200 OK\r\n"
                f"Via: {via_hdr}\r\n"
                f"From: {from_hdr}\r\n"
                f"To: {to_hdr}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Contact: <sip:{self.worker.user}@{PUBLIC_IP}:{self.worker.port}>\r\n"
                f"Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp_body)}\r\n\r\n"
                f"{sdp_body}"
            )
            self.transport.sendto(response.encode('utf-8'), addr)
            log_info(f"Звонок принят. SDP отвечен с публичным IP: {PUBLIC_IP}")

        elif "ACK sip:" in msg:
            log_info("ACK получен. Разговор начался.")

        elif "BYE sip:" in msg:
            log_info("Клиент повесил трубку (BYE)")
            asyncio.create_task(self.worker.stop_current_call())

class SIPWorker:
    def __init__(self):
        self.host = PLUSOFON_SIP_HOST
        self.port = 5060
        self.user = PLUSOFON_SIP_USER
        self.password = PLUSOFON_SIP_PASSWORD
        self.transport = None
        self.is_running = False
        self.active_rtp_proto = None
        self.active_rtp_transport = None
        self.current_phone = "Неизвестный"
        self.current_session_id = "default"

    def generate_sdp(self, rtp_port: int) -> str:
        return (
            "v=0\r\n"
            f"o=- {random.randint(10000,99999)} {random.randint(10000,99999)} IN IP4 {PUBLIC_IP}\r\n"
            "s=Woltron AI Bot\r\n"
            f"c=IN IP4 {PUBLIC_IP}\r\n"
            "t=0 0\r\n"
            f"m=audio {rtp_port} RTP/AVP 8 101\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=sendrecv\r\n"
        )

    async def start_rtp_session(self, session_id, rtp_port, phone, remote_ip, remote_port):
        self.current_phone = phone
        self.current_session_id = session_id
        loop = asyncio.get_running_loop()

        if self.active_rtp_transport and not self.active_rtp_transport.is_closing():
            if remote_ip and remote_port and self.active_rtp_proto:
                self.active_rtp_proto.remote_target = (remote_ip, remote_port)
                log_info(f"RTP цель обновлена для re-INVITE: {remote_ip}:{remote_port}")
            return

        transport = None
        protocol = None

        for port in range(rtp_port, rtp_port + 50):
            try:
                transport, protocol = await loop.create_datagram_endpoint(
                    lambda: RTPProtocol(session_id),
                    local_addr=('0.0.0.0', port)
                )
                log_info(f"RTP Сессия открыта на порту {port}")
                break
            except OSError as e:
                if e.errno == 98:
                    continue
                else:
                    log_error(f"Ошибка открытия RTP сокета: {e}")
                    raise e

        if not transport:
            log_error("Не удалось найти свободный RTP порт")
            return

        if remote_ip and remote_port:
            protocol.remote_target = (remote_ip, remote_port)
            log_info(f"RTP Цель зафиксирована из SDP: {remote_ip}:{remote_port}")

        self.active_rtp_transport = transport
        self.active_rtp_proto = protocol

        asyncio.create_task(self.vad_and_dialog_loop())

    async def vad_and_dialog_loop(self):
        proto = self.active_rtp_proto
        SILENCE_THRESHOLD = 450
        silence_start_time = None

        while self.is_running and self.active_rtp_proto == proto:
            await asyncio.sleep(0.03)

            if proto.is_processing or len(proto.pcm_buffer) < 3200:
                continue

            recent_samples = bytes(proto.pcm_buffer[-1600:])
            rms = audioop.rms(recent_samples, 2)

            if rms < SILENCE_THRESHOLD:
                if silence_start_time is None:
                    silence_start_time = time.time()
                elif time.time() - silence_start_time >= 0.25:
                    proto.is_processing = True
                    audio_to_process = bytes(proto.pcm_buffer)
                    proto.pcm_buffer.clear()

                    log_info("Пауза в речи обнаружена (RMS). Отправка в Yandex STT...")
                    text = await transcribe_audio_yandex(audio_to_process)

                    if text:
                        reply_text = await ask_yandex_gpt(text, self.current_session_id)
                        tts_pcm = await synthesize_speech_yandex(reply_text, self.tts_stub, self.folder_id)
                   
                        if tts_pcm:
                            log_info("Воспроизведение ответа бота...")
                            await proto.send_audio_response(tts_pcm)

                    proto.pcm_buffer.clear()
                    silence_start_time = None
                    proto.is_processing = False
            else:
                silence_start_time = None

    async def stop_current_call(self):
        if not self.active_rtp_transport:
            log_info("Звонок уже завершен или не активен, игнорируем дублирующий BYE.")
            return

        self.active_rtp_transport.close()
        self.active_rtp_transport = None

        transcript = await get_session_history_formatted(self.current_session_id)
        parsed_lead_data = await generate_call_summary(transcript)
     
        await send_lead_to_albato(
            phone=self.current_phone,
            summary=parsed_lead_data.get("summary", ""),
            transcript=transcript,
            session_id=self.current_session_id,
            details=parsed_lead_data
        )
   
        await clear_session_context(self.current_session_id)
        log_info(f"Звонок {self.current_session_id} полностью обработан и сохранен.")

    async def send_register(self):
        if not self.transport:
            return
        call_id = f"{random.randint(100000, 999999)}@woltron"
        sip_msg = (
            f"REGISTER sip:{self.host} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {PUBLIC_IP}:{self.port};branch=z9hG4bK{random.randint(1000,9999)}\r\n"
            f"From: <sip:{self.user}@{self.host}>;tag={random.randint(1000,9999)}\r\n"
            f"To: <sip:{self.user}@{self.host}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 1 REGISTER\r\n"
            f"Contact: <sip:{self.user}@{PUBLIC_IP}:{self.port}>\r\n"
            f"Max-Forwards: 70\r\n"
            f"Expires: 120\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        try:
            target_ip = socket.gethostbyname(self.host)
            self.transport.sendto(sip_msg.encode('utf-8'), (target_ip, 5060))
        except Exception as e:
            log_error(f"Ошибка REGISTER: {e}")

    async def register_loop(self):
        while self.is_running:
            await self.send_register()
            await asyncio.sleep(45)

    async def start(self):
        self.is_running = True
        loop = asyncio.get_running_loop()
   
        await loop.create_datagram_endpoint(
            lambda: SIPProtocol(self),
            local_addr=('0.0.0.0', self.port)
        )
   
        asyncio.create_task(self.register_loop())
        log_info("SIP/RTP Движок запущен и ready.")

        while self.is_running:
            await asyncio.sleep(1)

if __name__ == "__main__":
    worker = SIPWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        log_info("SIP Worker остановлен.")
