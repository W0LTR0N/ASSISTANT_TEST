import asyncio
import grpc
from google.protobuf.json_format import ParseDict
from config import YANDEX_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

# Импортируем proto-сообщения и gRPC-сервис
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    log_info(f"TTS v3 gRPC: Синтез для текста: {clean_text[:50]}...")

    # Авторизация по API-Key через gRPC Metadata
    metadata = (("authorization", f"Api-Key {YANDEX_API_KEY}"),)
    if YANDEX_FOLDER_ID:
        metadata += (("x-folder-id", YANDEX_FOLDER_ID),)

    # Точный JSON-запрос для ParseDict (с валидированными поп-полями camelCase)
    request_dict = {
        "text": clean_text,
        "outputAudioSpec": {
            "rawAudio": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        },
        "hints": [
            {"voice": "filipp"}
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

        log_info(f"TTS v3 gRPC: Успешно собрано {len(pcm_data)} байт ровного PCM!")
        return bytes(pcm_data)

    except Exception as e:
        log_error(f"Исключение TTS v3 gRPC: {str(e)}")
        return b""
