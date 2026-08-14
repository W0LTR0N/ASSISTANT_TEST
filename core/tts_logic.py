import asyncio
import grpc
from google.protobuf.json_format import ParseDict
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

from yandex.cloud.ai.tts.v3 import tts_pb2, tts_pb2_grpc

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    log_info(f"TTS v3 gRPC: Синтез для текста: {clean_text[:50]}...")

    metadata = (("authorization", f"Api-Key {YANDEX_GPT_API_KEY}"),)
    if YANDEX_FOLDER_ID:
        metadata += (("x-folder-id", YANDEX_FOLDER_ID),)

    # Собираем структуру запроса через обычный Python dict
    # Это 100% обходит проблемы с отсутствующими атрибутами в protobuf-модулях
    request_dict = {
        "text": clean_text,
        "output_audio_spec": {
            "container_audio_spec": {
                "format": "WAV"
            }
        },
        "hints": [
            {"voice": "alexander"}
        ]
    }

    request = ParseDict(request_dict, tts_pb2.UtteranceSynthesisRequest())
    audio_bytes = bytearray()

    try:
        async with grpc.aio.secure_channel(
            "tts.api.cloud.yandex.net:443",
            grpc.ssl_channel_credentials()
        ) as channel:
            stub = tts_pb2_grpc.SynthesizerStub(channel)
            stream = stub.UtteranceSynthesis(request, metadata=metadata)
           
            async for response in stream:
                if response.audio_chunk and response.audio_chunk.data:
                    audio_bytes.extend(response.audio_chunk.data)

        if len(audio_bytes) == 0:
            log_error("TTS v3 gRPC: Получен пустой аудиопоток!")
            return b""

        # Получили WAV-поток. Срезаем 44 байта заголовка RIFF, чтобы выдать чистый PCM
        pcm_data = bytes(audio_bytes)
        if len(pcm_data) > 44 and pcm_data[:4] == b'RIFF':
            pcm_data = pcm_data[44:]

        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        log_info(f"TTS v3 gRPC: Успешно собрано {len(pcm_data)} байт PCM!")
        return pcm_data

    except Exception as e:
        log_error(f"Исключение TTS v3 gRPC: {str(e)}")
        return b""
