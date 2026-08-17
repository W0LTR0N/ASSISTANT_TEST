import asyncio
import os
import wave
import audioop
import grpc
from google.protobuf.json_format import ParseDict
from config import YANDEX_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

# Импортируем proto-сообщения и gRPC-сервис
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc

# Путь к фоновому шуму в корневой папке проекта
BG_NOISE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "background_noise.wav")
_bg_noise_cache = None

def get_background_noise_pcm(target_len: int) -> bytes:
    global _bg_noise_cache
    if _bg_noise_cache is None:
        if not os.path.exists(BG_NOISE_PATH):
            log_error(f"Файл фонового шума не найден по пути: {BG_NOISE_PATH}")
            return b""
        try:
            with wave.open(BG_NOISE_PATH, "rb") as wf:
                # Читаем wav и конвертируем в сырой PCM (8000Hz, 16-bit, mono) если нужно,
                # или берем готовые кадры, если файл уже в нужном формате
                raw_data = wf.readframes(wf.getnframes())
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()

                # Конвертируем в моно и 8000 Гц при необходимости через audioop
                if channels == 2:
                    raw_data = audioop.tomono(raw_data, sample_width, 0.5, 0.5)
                if framerate != 8000:
                    raw_data, _ = audioop.ratecv(raw_data, sample_width, 1, framerate, 8000, None)
               
                _bg_noise_cache = raw_data
                log_info(f"Фоновый шум успешно загружен и закэширован из {BG_NOISE_PATH}")
        except Exception as e:
            log_error(f"Ошибка чтения background_noise.wav: {e}")
            return b""

    if not _bg_noise_cache:
        return b""

    # Зацикливаем фоновый шум под длину речи бота
    loops = target_len // len(_bg_noise_cache) + 1
    full_noise = _bg_noise_cache * loops
    return full_noise[:target_len]

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    log_info(f"TTS v3 gRPC: Синтез для текста (Марат): {clean_text[:50]}...")

    # Авторизация по API-Key через gRPC Metadata
    metadata = (("authorization", f"Api-Key {YANDEX_API_KEY}"),)
    if YANDEX_FOLDER_ID:
        metadata += (("x-folder-id", YANDEX_FOLDER_ID),)

    # Точный JSON-запрос для ParseDict с голосом marat
    request_dict = {
        "text": clean_text,
        "outputAudioSpec": {
            "rawAudio": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        },
        "hints": [
            {"voice": "marat"}
        ]
    }

    request = ParseDict(request_dict, tts_pb2.UtteranceSynthesisRequest())
    pcm_data = bytearray()

    try:
        # Создаем чисто асинхронный gRPC канал
        async with grpc.aio.secure_channel(
            "tts.api.cloud.yandex.net:443",
            grpc.ssl_channel_credentials()
        ) as channel:
            # tts_service_pb2_grpc гарантированно содержит SynthesizerStub
            stub = tts_service_pb2_grpc.SynthesizerStub(channel)
           
            # Асинхронно читаем бинарный поток байтов
            stream = stub.UtteranceSynthesis(request, metadata=metadata)
            async for response in stream:
                if response.audio_chunk and response.audio_chunk.data:
                    pcm_data.extend(response.audio_chunk.data)

        if len(pcm_data) == 0:
            log_error("TTS v3 gRPC: Получен пустой аудиопоток!")
            return b""

        # Выравнивание под 16-bit PCM (по 2 байта на сэмпл для ровной передачи в SIP)
        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        speech_bytes = bytes(pcm_data)

        # Аккуратно подмешиваем фоновый шум из корневого файла (голос основной 85%, шум 15%)
        bg_bytes = get_background_noise_pcm(len(speech_bytes))
        if bg_bytes:
            try:
                speech_bytes = audioop.add(speech_bytes, bg_bytes, 2, 0.85, 0.15)
                log_info("Фоновый шум успешно наложен на речь Марата.")
            except Exception as mix_err:
                log_error(f"Ошибка микширования фонового шума: {mix_err}")

        log_info(f"TTS v3 gRPC: Успешно собрано {len(speech_bytes)} байт ровного PCM!")
        return speech_bytes

    except Exception as e:
        log_error(f"Исключение TTS v3 gRPC: {str(e)}")
        return b""
