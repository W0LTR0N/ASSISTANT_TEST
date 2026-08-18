import asyncio
import socket
import struct
import random
import signal
import time
import re
import hashlib
import audioop
from config import *
from core.logger import log_info, log_error
from core.tts_logic import synthesize_speech, close_genvoice_session
from core.stt_logic import transcribe_audio_yandex, close_stt_session
from core.gpt_logic import (
    ask_yandex_gpt, seed_greeting, get_session_history_formatted,
    generate_call_summary, clear_session_context, close_gpt_session,
    cleanup_old_sessions,
)
from core.albato_sender import send_lead_to_albato, resend_failed_leads, close_albato_session


class RTPProtocol(asyncio.DatagramProtocol):
    def __init__(self, worker, call_id, phone, remote_ip, remote_port):
        self.worker = worker
        self.call_id = call_id
        self.phone = phone
        self.remote_target = (remote_ip, remote_port)
        self.transport = None
        self.active = True
        self.speaking = False
        self.pcm_buffer = bytearray()
        self.ssrc = random.randint(0, 0xFFFFFFFF)
        self.sequence_number = random.randint(0, 0xFFFF)
        self.timestamp = random.randint(0, 0xFFFFFFFF)
        self.last_packet_time = time.time()
        self.is_processing = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if not self.active:
            return
        self.last_packet_time = time.time()
        if len(data) < 12:
            return
        if self.speaking:
            return
        payload_type = data[1] & 0x7F
        cc = data[0] & 0x0F
        x_bit = (data[0] >> 4) & 0x1
        offset = 12 + cc * 4
        if x_bit and len(data) >= offset + 4:
            ext_len_words = struct.unpack("!H", data[offset + 2:offset + 4])[0]
            offset += 4 + ext_len_words * 4
        if offset > len(data):
            return
        payload = data[offset:]
        try:
            if payload_type == 8:
                pcm_frame = audioop.alaw2lin(payload, 2)
            elif payload_type == 0:
                pcm_frame = audioop.ulaw2lin(payload, 2)
            else:
                return
            self.pcm_buffer.extend(pcm_frame)
        except Exception as e:
            log_error(f"Ошибка декодирования RTP (PT={payload_type}): {e}")

    async def send_audio_response(self, pcm_data):
        if not self.remote_target or not pcm_data:
            return
        self.speaking = True
        self.pcm_buffer.clear()
        frame_bytes = 320
        start_time = time.monotonic()
        frame_index = 0
        for i in range(0, len(pcm_data), frame_bytes):
            chunk = pcm_data[i:i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk = chunk + b'\x00' * (frame_bytes - len(chunk))
            alaw = audioop.lin2alaw(chunk, 2)
            marker = 0x80 if frame_index == 0 else 0x00
            header = struct.pack(
                "!BBHII",
                0x80,
                marker | 0x08,
                self.sequence_number & 0xFFFF,
                self.timestamp & 0xFFFFFFFF,
                self.ssrc,
            )
            self.sequence_number += 1
            self.timestamp += 160
            try:
                self.transport.sendto(header + alaw, self.remote_target)
            except Exception as e:
                log_error(f"Ошибка отправки RTP: {e}")
                break
            frame_index += 1
            delay = start_time + frame_index * 0.02 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        self.speaking = False
        self.pcm_buffer.clear()

    def send_keepalive(self):
        if not self.transport or not self.remote_target or self.speaking or not self.active:
            return
        payload = b'\xd5' * 160
        header = struct.pack(
            "!BBHII",
            0x80, 0x08,
            self.sequence_number & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc,
        )
        self.sequence_number += 1
        self.timestamp += 160
        try:
            self.transport.sendto(header + payload, self.remote_target)
        except Exception:
            pass


class SIPProtocol(asyncio.DatagramProtocol):
    def __init__(self, worker):
        self.worker = worker
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = data.decode('utf-8', errors='ignore')
            first_line = msg.split('\r\n', 1)[0]
            if first_line.startswith('SIP/2.0'):
                self._handle_response(first_line, msg)
                return
            method = first_line.split(' ', 1)[0].upper()
            if method == 'INVITE':
                self._handle_invite(msg, addr)
            elif method == 'ACK':
                cid = self._extract_header(msg, "Call-ID") or "?"
                log_info(f"[{cid}] ACK получен, сессия подтверждена (CONFIRMED)")
                session = self.worker.sessions.get(cid)
                if session:
                    session["confirmed"] = True
            elif method == 'BYE':
                self._handle_bye(msg, addr)
            elif method == 'CANCEL':
                self._handle_cancel(msg, addr)
            elif method == 'OPTIONS':
                self._handle_options(msg, addr)
        except Exception as e:
            log_error(f"Ошибка обработки SIP-сообщения: {e}")

    def _handle_response(self, first_line, msg):
        parts = first_line.split()
        code = parts[1] if len(parts) > 1 else ""
        cseq = self._extract_header(msg, "CSeq") or ""
        call_id = self._extract_header(msg, "Call-ID") or ""
        if code in ("401", "407"):
            if "REGISTER" in cseq:
                asyncio.create_task(self.worker.handle_register_challenge(msg))
            elif "BYE" in cseq:
                asyncio.create_task(self.worker.handle_bye_challenge(msg, call_id))
            else:
                log_error(f"Получен {code} на {cseq} — авторизация для метода не реализована")
        elif code == "200":
            if "REGISTER" in cseq:
                log_info("REGISTER принят: регистрация на провайдере успешна.")
            elif "BYE" in cseq:
                self.worker.pending_byes.pop(call_id, None)
        elif code and code[0] in ("4", "5", "6") and "REGISTER" in cseq:
            log_error(f"REGISTER отклонён провайдером: {first_line}")

    def _send_error_response(self, code, reason, via_hdr, from_hdr, to_hdr, call_id, cseq, addr):
        if ";tag=" not in to_hdr.lower():
            to_hdr = f"{to_hdr};tag=woltron{random.randint(100000, 999999)}"
        response = (
            f"SIP/2.0 {code} {reason}\r\n"
            f"Via: {via_hdr}\r\n"
            f"From: {from_hdr}\r\n"
            f"To: {to_hdr}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self.transport.sendto(response.encode('utf-8'), addr)
        return response

    def _handle_invite(self, msg, addr):
        # Защита от сканеров: принимаем INVITE только с доверенных IP (Плюсофон).
        # Если TRUSTED_SIP_IPS пуст — принимаем всех (старое поведение).
        if TRUSTED_SIP_IPS and addr[0] not in TRUSTED_SIP_IPS:
            log_error(f"INVITE с недоверенного IP {addr[0]} — вызов отклонён")
            return
        log_info(f"Входящий вызов от {addr}")
        call_id = self._extract_header(msg, "Call-ID") or f"unknown-{random.randint(1000,9999)}"
        cseq = self._extract_header(msg, "CSeq") or "1 INVITE"
        from_hdr = self._extract_header(msg, "From") or ""
        to_hdr = self._extract_header(msg, "To") or ""
        via_hdr = self._extract_header(msg, "Via") or f"SIP/2.0/UDP {addr[0]}:{addr[1]}"

        existing = self.worker.sessions.get(call_id)
        if existing:
            log_info("Повторный INVITE (ретрансмит), повторно шлём 200 OK")
            last_200 = existing.get("last_200")
            if last_200:
                self.transport.sendto(last_200.encode('utf-8'), addr)
            return

        trying = (
            f"SIP/2.0 100 Trying\r\nVia: {via_hdr}\r\nFrom: {from_hdr}\r\n"
            f"To: {to_hdr}\r\nCall-ID: {call_id}\r\nCSeq: {cseq}\r\nContent-Length: 0\r\n\r\n"
        )
        self.transport.sendto(trying.encode('utf-8'), addr)

        phone_m = re.search(r'sip:\+?(\d+)', from_hdr)
        phone = phone_m.group(1) if phone_m else "Неизвестный"
        remote_ip, remote_port = self.parse_sdp_remote_media(msg)
        if not remote_ip:
            remote_ip = addr[0]

        if not remote_port:
            log_error(f"[{call_id}] В SDP нет медиа-порта, отправляем 488 Not Acceptable Here")
            self._send_error_response(
                488, "Not Acceptable Here",
                via_hdr, from_hdr, to_hdr, call_id, cseq, addr
            )
            return

        rtp_port = self.worker.reserve_port()
        if rtp_port is None:
            log_error(f"[{call_id}] Нет свободных RTP-портов, отправляем 503 Service Unavailable")
            self._send_error_response(
                503, "Service Unavailable",
                via_hdr, from_hdr, to_hdr, call_id, cseq, addr
            )
            return

        if ";tag=" not in to_hdr.lower():
            to_hdr = f"{to_hdr};tag=woltron{random.randint(100000, 999999)}"

        self.worker.sessions[call_id] = {
            "call_id": call_id,
            "proto": None,
            "transport": None,
            "phone": phone,
            "rtp_port": rtp_port,
            "started_at": time.time(),
            "signaling_addr": addr,
            "from_hdr": from_hdr,
            "to_hdr": to_hdr,
            "via_hdr": via_hdr,
            "invite_cseq": cseq,
            "bye_cseq": 1,
            "last_200": None,
            "confirmed": False,
            "stopped": False,
        }

        asyncio.create_task(self.worker.start_rtp_session(call_id, rtp_port, phone, remote_ip, remote_port))

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
            f"Content-Length: {len(sdp_body)}\r\n\r\n{sdp_body}"
        )
        self.worker.sessions[call_id]["last_200"] = response
        self.transport.sendto(response.encode('utf-8'), addr)

    def _handle_bye(self, msg, addr):
        call_id = self._extract_header(msg, "Call-ID") or ""
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {self._extract_header(msg, 'Via') or ''}\r\n"
            f"From: {self._extract_header(msg, 'From') or ''}\r\n"
            f"To: {self._extract_header(msg, 'To') or ''}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {self._extract_header(msg, 'CSeq') or '1 BYE'}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self.transport.sendto(resp.encode('utf-8'), addr)
        asyncio.create_task(self.worker.stop_call(call_id, send_bye=False))

    def _handle_cancel(self, msg, addr):
        call_id = self._extract_header(msg, "Call-ID") or ""
        via_hdr = self._extract_header(msg, "Via") or ""
        from_hdr = self._extract_header(msg, "From") or ""
        to_hdr = self._extract_header(msg, "To") or ""
        ok = (
            f"SIP/2.0 200 OK\r\nVia: {via_hdr}\r\nFrom: {from_hdr}\r\nTo: {to_hdr}\r\n"
            f"Call-ID: {call_id}\r\nCSeq: {self._extract_header(msg, 'CSeq') or '1 CANCEL'}\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self.transport.sendto(ok.encode('utf-8'), addr)
        session = self.worker.sessions.get(call_id)
        inv_cseq = session["invite_cseq"] if session else "1 INVITE"
        inv_to = session["to_hdr"] if session else to_hdr
        req_term = (
            f"SIP/2.0 487 Request Terminated\r\nVia: {via_hdr}\r\nFrom: {from_hdr}\r\n"
            f"To: {inv_to}\r\nCall-ID: {call_id}\r\nCSeq: {inv_cseq}\r\nContent-Length: 0\r\n\r\n"
        )
        self.transport.sendto(req_term.encode('utf-8'), addr)
        asyncio.create_task(self.worker.stop_call(call_id, send_bye=False))

    def _handle_options(self, msg, addr):
        call_id = self._extract_header(msg, "Call-ID") or "0"
        cseq = self._extract_header(msg, "CSeq") or "1 OPTIONS"
        from_hdr = self._extract_header(msg, "From") or ""
        to_hdr = self._extract_header(msg, "To") or ""
        via_hdr = self._extract_header(msg, "Via") or f"SIP/2.0/UDP {addr[0]}:{addr[1]}"
        if ";tag=" not in to_hdr.lower():
            to_hdr = f"{to_hdr};tag={random.randint(1000,9999)}"
        response = (
            f"SIP/2.0 200 OK\r\nVia: {via_hdr}\r\nFrom: {from_hdr}\r\nTo: {to_hdr}\r\n"
            f"Call-ID: {call_id}\r\nCSeq: {cseq}\r\n"
            f"Contact: <sip:{self.worker.user}@{PUBLIC_IP}:{self.worker.port}>\r\n"
            f"Allow: INVITE, ACK, BYE, CANCEL, OPTIONS\r\nContent-Length: 0\r\n\r\n"
        )
        self.transport.sendto(response.encode('utf-8'), addr)

    def _extract_header(self, msg, header_name):
        pattern = rf'^{header_name}:\s*(.+)$'
        match = re.search(pattern, msg, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else None

    def parse_sdp_remote_media(self, msg):
        session_ip = None
        media_ip = None
        port = None
        in_media = False
        for line in msg.splitlines():
            if line.startswith("c=IN IP4"):
                if in_media:
                    media_ip = line.split()[2]
                else:
                    session_ip = line.split()[2]
            elif line.startswith("m=audio"):
                in_media = True
                try:
                    port = int(line.split()[1])
                except Exception:
                    port = None
        return (media_ip or session_ip), port


class SIPWorker:
    def __init__(self):
        self.host = PLUSOFON_SIP_HOST
        self.port = 5060
        self.user = PLUSOFON_SIP_USER
        self.password = PLUSOFON_SIP_PASSWORD
        self.transport = None
        self.is_running = False
        self.sessions = {}
        self.pending_byes = {}
        self.used_ports = set()
        self._last_allocated_port = RTP_PORT_MIN
        self.folder_id = YANDEX_FOLDER_ID
        self.register_state = {
            "call_id": f"{random.randint(100000,999999)}@{self.host}",
            "from_tag": f"tag{random.randint(100000,999999)}",
            "cseq": 1,
        }
        self.auth_cache = None

    def reserve_port(self):
        total_ports = RTP_PORT_MAX - RTP_PORT_MIN + 1
        for _ in range(total_ports):
            port = self._last_allocated_port
            self._last_allocated_port += 1
            if self._last_allocated_port > RTP_PORT_MAX:
                self._last_allocated_port = RTP_PORT_MIN
            if port not in self.used_ports:
                self.used_ports.add(port)
                return port
        return None

    def release_port(self, port):
        self.used_ports.discard(port)

    async def start_rtp_session(self, call_id, rtp_port, phone, remote_ip, remote_port):
        loop = asyncio.get_running_loop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', rtp_port))
        except OSError as e:
            sock.close()
            log_error(f"[{call_id}] Не удалось забиндить RTP порт {rtp_port}: {e}, пробуем следующий")
            self.release_port(rtp_port)
            new_port = self.reserve_port()
            if new_port is None:
                log_error(f"[{call_id}] Нет свободных RTP-портов")
                await self.stop_call(call_id, send_bye=True, send_lead=False)
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', new_port))
            except OSError as e2:
                sock.close()
                log_error(f"[{call_id}] Повторная ошибка bind на {new_port}: {e2}")
                self.release_port(new_port)
                await self.stop_call(call_id, send_bye=True, send_lead=False)
                return
            rtp_port = new_port

        sock.setblocking(False)
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: RTPProtocol(self, call_id, phone, remote_ip, remote_port),
                sock=sock,
            )
        except Exception as e:
            sock.close()
            log_error(f"[{call_id}] Ошибка создания RTP endpoint: {e}")
            self.release_port(rtp_port)
            await self.stop_call(call_id, send_bye=True, send_lead=False)
            return

        session_data = self.sessions.get(call_id)
        if not session_data:
            transport.close()
            return
        session_data["proto"] = protocol
        session_data["transport"] = transport

        asyncio.create_task(self.dialog_loop(call_id))

        greeting_text = "Woltron Detailing, здравствуйте! Меня зовут Филипп, слушаю вас."
        seed_greeting(call_id, greeting_text)
        try:
            tts_pcm = await synthesize_speech(greeting_text, session_id=call_id)
            if tts_pcm:
                await protocol.send_audio_response(tts_pcm)
        except Exception as e:
            log_error(f"[{call_id}] Ошибка озвучки приветствия: {e}")

    async def dialog_loop(self, call_id):
        session = self.sessions.get(call_id)
        if not session:
            return
        proto = session["proto"]
        speech_seen = False
        speech_start = None
        last_speech_time = None
        silence_start_time = None
        last_keepalive = time.time()

        while proto.active:
            await asyncio.sleep(0.02)
            now = time.time()
            if now - proto.last_packet_time > IDLE_CALL_TIMEOUT:
                log_info(f"[{call_id}] Сессия неактивна {IDLE_CALL_TIMEOUT}с, закрываем принудительно")
                await self.stop_call(call_id, send_bye=True)
                break
            if now - last_keepalive >= 5:
                proto.send_keepalive()
                last_keepalive = now
            if proto.is_processing or proto.speaking:
                continue
            if len(proto.pcm_buffer) < 320:
                continue

            recent = bytes(proto.pcm_buffer[-320:])
            try:
                rms = audioop.rms(recent, 2)
            except Exception:
                rms = 0

            if rms >= SILENCE_THRESHOLD:
                if not speech_seen:
                    speech_seen = True
                    speech_start = now
                last_speech_time = now
                silence_start_time = None
            else:
                if speech_seen:
                    if silence_start_time is None:
                        silence_start_time = now
                    elif now - silence_start_time >= SILENCE_TO_FINISH:
                        speech_dur = (last_speech_time - speech_start) if last_speech_time else 0.0
                        await self._process_utterance(call_id, session, proto, speech_dur)
                        speech_seen = False
                        silence_start_time = None
                        speech_start = None
                        last_speech_time = None
                else:
                    if len(proto.pcm_buffer) > 6400:
                        del proto.pcm_buffer[:len(proto.pcm_buffer) - 3200]

            if speech_seen and speech_start and (now - speech_start) >= MAX_UTTERANCE_SEC:
                speech_dur = (last_speech_time - speech_start) if last_speech_time else 0.0
                await self._process_utterance(call_id, session, proto, speech_dur)
                speech_seen = False
                silence_start_time = None
                speech_start = None
                last_speech_time = None

    async def _process_utterance(self, call_id, session, proto, speech_dur):
        if speech_dur < MIN_SPEECH_SEC:
            proto.pcm_buffer.clear()
            return

        if call_id not in self.sessions:
            log_info(f"[{call_id}] Звонок завершён до начала обработки реплики")
            proto.pcm_buffer.clear()
            return

        pcm_data = bytes(proto.pcm_buffer)
        proto.pcm_buffer.clear()
        proto.is_processing = True
        t0 = time.monotonic()
        t1 = t2 = t0

        try:
            text = await transcribe_audio_yandex(pcm_data, call_id)
            t1 = time.monotonic()
            if not text:
                return

            if call_id not in self.sessions:
                log_info(f"[{call_id}] Звонок завершён во время STT, пропускаем")
                return

            reply_text = await ask_yandex_gpt(text, call_id)
            t2 = time.monotonic()

            if call_id not in self.sessions:
                log_info(f"[{call_id}] Звонок завершён во время GPT, пропускаем TTS")
                return

            tts_pcm = await synthesize_speech(reply_text, session_id=call_id)
            t3 = time.monotonic()
            log_info(f"[{call_id}] Latency STT={t1 - t0:.2f}s GPT={t2 - t1:.2f}s TTS={t3 - t2:.2f}s TOTAL={t3 - t0:.2f}s")

            if call_id not in self.sessions:
                log_info(f"[{call_id}] Звонок завершён перед отправкой TTS, пропускаем")
                return

            if tts_pcm:
                await proto.send_audio_response(tts_pcm)
        except KeyError:
            log_info(f"[{call_id}] Звонок завершён во время обработки (race condition), пропускаем")
        except Exception as e:
            log_error(f"[{call_id}] Исключение в цикле диалога: {e}")
        finally:
            proto.is_processing = False

    async def send_bye(self, session):
        addr = session.get("signaling_addr")
        if not addr or not self.transport:
            return
        target_user = session["phone"] if session["phone"] != "Неизвестный" else self.user
        uri = f"sip:{target_user}@{addr[0]}:{addr[1]}"
        bye_cseq = session.get("bye_cseq", 1)
        branch = f"z9hG4bK{random.randint(100000,999999)}"
        bye = (
            f"BYE {uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {PUBLIC_IP}:{self.port};branch={branch}\r\n"
            f"From: {session['to_hdr']}\r\n"
            f"To: {session['from_hdr']}\r\n"
            f"Call-ID: {session['call_id']}\r\n"
            f"CSeq: {bye_cseq} BYE\r\n"
            f"Max-Forwards: 70\r\nContent-Length: 0\r\n\r\n"
        )
        self.pending_byes[session["call_id"]] = {
            "addr": addr, "uri": uri, "cseq": bye_cseq,
            "from_hdr": session["to_hdr"], "to_hdr": session["from_hdr"],
        }
        session["bye_cseq"] = bye_cseq + 1
        try:
            self.transport.sendto(bye.encode('utf-8'), addr)
        except Exception as e:
            log_error(f"Ошибка отправки BYE: {e}")

    async def handle_bye_challenge(self, msg, call_id):
        pending = self.pending_byes.get(call_id)
        if not pending:
            return
        auth_data, proxy = self._parse_auth_challenge(msg)
        if not auth_data:
            log_error("401/407 на BYE, но не удалось разобрать challenge")
            return
        auth_header = self._compute_digest(auth_data, method="BYE", uri=pending["uri"], proxy=proxy)
        self.pending_byes.pop(call_id, None)
        branch = f"z9hG4bK{random.randint(100000,999999)}"
        bye = (
            f"BYE {pending['uri']} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {PUBLIC_IP}:{self.port};branch={branch}\r\n"
            f"From: {pending['from_hdr']}\r\n"
            f"To: {pending['to_hdr']}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {pending['cseq']} BYE\r\n"
            f"{auth_header}\r\n"
            f"Max-Forwards: 70\r\nContent-Length: 0\r\n\r\n"
        )
        log_info(f"[{call_id}] Повторяем BYE с digest-авторизацией")
        try:
            self.transport.sendto(bye.encode('utf-8'), pending["addr"])
        except Exception as e:
            log_error(f"Ошибка повторной отправки BYE: {e}")

    async def stop_call(self, call_id, send_bye=False, send_lead=True):
        session = self.sessions.pop(call_id, None)
        if not session:
            log_info(f"[{call_id}] Звонок уже завершён или не найден")
            return
        if session.get("stopped"):
            log_info(f"[{call_id}] Звонок уже отмечен как завершённый (stopped=True)")
            return
        session["stopped"] = True

        if session.get("proto"):
            session["proto"].active = False
        if session.get("transport"):
            try:
                session["transport"].close()
            except Exception:
                pass
        self.release_port(session["rtp_port"])
        if send_bye:
            await self.send_bye(session)

        if not send_lead:
            clear_session_context(call_id)
            log_info(f"[{call_id}] Звонок завершён технически, без лида.")
            return

        phone = session["phone"]
        try:
            transcript = get_session_history_formatted(call_id)
            client_spoke = any(t["role"] == "client" for t in transcript)
            if not client_spoke:
                log_info(f"[{call_id}] Разговора не было (клиент молчал) — лид в Albato НЕ отправлен")
            else:
                parsed_lead_data = await generate_call_summary(transcript, session_id=call_id)
                await send_lead_to_albato(
                    phone=phone,
                    summary=parsed_lead_data.get("summary", ""),
                    transcript=transcript,
                    session_id=call_id,
                    details=parsed_lead_data,
                )
        except Exception as e:
            log_error(f"[{call_id}] Ошибка при обработке завершения звонка: {e}")
        finally:
            clear_session_context(call_id)
            log_info(f"[{call_id}] Звонок полностью обработан и сохранён.")

    def generate_sdp(self, rtp_port):
        return f"""v=0
o=- {int(time.time())} 1 IN IP4 {PUBLIC_IP}
s=-
c=IN IP4 {PUBLIC_IP}
t=0 0
m=audio {rtp_port} RTP/AVP 8
a=rtpmap:8 PCMA/8000
a=sendrecv
"""

    def _build_register_message(self, auth_header=None):
        branch = f"z9hG4bK{random.randint(100000,999999)}"
        headers = (
            f"REGISTER sip:{self.host} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {PUBLIC_IP}:{self.port};branch={branch}\r\n"
            f"From: <sip:{self.user}@{self.host}>;tag={self.register_state['from_tag']}\r\n"
            f"To: <sip:{self.user}@{self.host}>\r\n"
            f"Call-ID: {self.register_state['call_id']}\r\n"
            f"CSeq: {self.register_state['cseq']} REGISTER\r\n"
            f"Contact: <sip:{self.user}@{PUBLIC_IP}:{self.port}>\r\n"
            f"Max-Forwards: 70\r\n"
            f"Expires: 120\r\n"
        )
        if auth_header:
            headers += f"{auth_header}\r\n"
        headers += "Content-Length: 0\r\n\r\n"
        return headers

    def _compute_digest(self, auth_data, method="REGISTER", uri=None, proxy=False):
        if uri is None:
            uri = f"sip:{self.host}"
        realm = auth_data.get("realm", "")
        nonce = auth_data.get("nonce", "")
        qop = auth_data.get("qop")
        ha1 = hashlib.md5(f"{self.user}:{realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        header_name = "Proxy-Authorization" if proxy else "Authorization"
        if auth_data.get("_last_nonce") != nonce:
            auth_data["_nc_counter"] = 0
            auth_data["_last_nonce"] = nonce
        if qop:
            auth_data["_nc_counter"] = auth_data.get("_nc_counter", 0) + 1
            nc = f"{auth_data['_nc_counter']:08x}"
            cnonce = f"{random.randint(0, 0xFFFFFFFF):08x}"
            response = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
            auth_header = (
                f'{header_name}: Digest username="{self.user}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", response="{response}", '
                f'qop={qop}, nc={nc}, cnonce="{cnonce}"'
            )
        else:
            response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
            auth_header = (
                f'{header_name}: Digest username="{self.user}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", response="{response}"'
            )
        if "opaque" in auth_data:
            auth_header += f', opaque="{auth_data["opaque"]}"'
        return auth_header

    def _parse_auth_challenge(self, msg):
        header, proxy = self._extract_challenge_header(msg)
        if not header:
            return None, False
        data = {}
        for match in re.finditer(r'(\w+)="?([^",]+)"?', header):
            data[match.group(1)] = match.group(2)
        return data, proxy

    def _extract_challenge_header(self, msg):
        m = re.search(r'Proxy-Authenticate:\s*Digest\s+(.*)', msg, re.I)
        if m:
            return m.group(1).strip(), True
        m = re.search(r'WWW-Authenticate:\s*Digest\s+(.*)', msg, re.I)
        if m:
            return m.group(1).strip(), False
        return None, False

    async def handle_register_challenge(self, msg):
        auth_data, proxy = self._parse_auth_challenge(msg)
        if not auth_data:
            log_error("Получен 401/407, но не удалось разобрать заголовок авторизации")
            return
        call_id_m = re.search(r'Call-ID:\s*(.+)', msg, re.I)
        call_id = call_id_m.group(1).strip() if call_id_m else None
        if call_id != self.register_state["call_id"]:
            log_info("401/407 относится не к текущей REGISTER-транзакции, игнорируем")
            return
        log_info("Получен запрос на авторизацию REGISTER, отправляем digest-ответ")
        self.auth_cache = auth_data
        self.auth_cache["_proxy"] = proxy
        auth_header = self._compute_digest(auth_data, proxy=proxy)
        self.register_state["cseq"] += 1
        await self._send_sip_message(self._build_register_message(auth_header))

    async def send_register(self):
        if self.auth_cache:
            auth_header = self._compute_digest(self.auth_cache, proxy=self.auth_cache.get("_proxy", False))
        else:
            auth_header = ""
        await self._send_sip_message(self._build_register_message(auth_header))

    async def _send_sip_message(self, sip_msg: str):
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(self.host, 5060, type=socket.SOCK_DGRAM)
            target_ip = infos[0][4][0]
            self.transport.sendto(sip_msg.encode('utf-8'), (target_ip, 5060))
        except Exception as e:
            log_error(f"Ошибка отправки SIP: {e}")

    async def register_loop(self):
        while self.is_running:
            await self.send_register()
            await asyncio.sleep(45)

    async def heartbeat_loop(self):
        while self.is_running:
            try:
                await asyncio.to_thread(self._write_heartbeat)
            except Exception as e:
                log_error(f"Не удалось записать heartbeat: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def _write_heartbeat(self):
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(str(time.time()))

    async def graceful_stop(self):
        if not self.is_running:
            return
        self.is_running = False
        log_info("Graceful shutdown: завершаем активные звонки и закрываем ресурсы...")
        for call_id in list(self.sessions.keys()):
            try:
                await self.stop_call(call_id, send_bye=True, send_lead=True)
            except Exception as e:
                log_error(f"Ошибка при завершении звонка {call_id}: {e}")
        if self.transport:
            self.transport.close()
        for closer in (close_stt_session, close_gpt_session, close_albato_session, close_genvoice_session):
            try:
                await closer()
            except Exception:
                pass
        log_info("SIP worker завершил работу.")

    async def start(self):
        if not SIP_CAN_START:
            log_error("КРИТИЧНО: PUBLIC_IP не задан или равен 127.0.0.1 — SIP worker не стартует.")
            raise SystemExit(2)
        self.is_running = True
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.graceful_stop()))
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: SIPProtocol(self),
            local_addr=('0.0.0.0', self.port),
        )
        self.transport = transport
        asyncio.create_task(self.register_loop())
        asyncio.create_task(self.heartbeat_loop())
        asyncio.create_task(resend_failed_leads())
        asyncio.create_task(cleanup_old_sessions())
        log_info("SIP/RTP Движок запущен и ready.")
        while self.is_running:
            await asyncio.sleep(1)


if __name__ == "__main__":
    worker = SIPWorker()
    try:
        asyncio.run(worker.start())
    except KeyboardInterrupt:
        log_info("SIP Worker остановлен.")
