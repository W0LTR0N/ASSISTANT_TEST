import asyncio
import grpc
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

# Импортируем только то, что точно работает
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_pb2_grpc

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    log_info(f"TTS v3 gRPC: Синтез для текста: {clean_text[:50]}...")

    metadata = (("authorization", f"Api-Key {YANDEX_GPT_API_KEY}"),)
    if YANDEX_FOLDER_ID:
        metadata += (("x-folder-id", YANDEX_FOLDER_ID),)

    # Используем tts_pb2 для создания структуры
    # Если класс RawAudioSpec недоступен напрямую, мы инициализируем AudioFormatOptions
    # и заполняем поля внутри него. Это обходит ошибку импорта.
    output_audio_spec = tts_pb2.AudioFormatOptions(
        raw_audio_spec=tts_pb2.RawAudioSpec(
            audio_encoding=tts_pb2.RawAudioSpec.LINEAR16_PCM,
            sample_rate_hertz=8000
        )
    )

    request = tts_pb2.UtteranceSynthesisRequest(
        text=clean_text,
        output_audio_spec=output_audio_spec,
        hints=[tts_pb2.Hints(voice="alexander")]
    )

    pcm_data = bytearray()

    try:
        async with grpc.aio.secure_channel(
            "tts.api.cloud.yandex.net:443",
            grpc.ssl_channel_credentials()
        ) as channel:
            stub = tts_pb2_grpc.SynthesizerStub(channel)
            stream = stub.UtteranceSynthesis(request, metadata=metadata)
           
            async for response in stream:
                if response.audio_chunk and response.audio_chunk.data:
                    pcm_data.extend(response.audio_chunk.data)

        if len(pcm_data) == 0:
            log_error("TTS v3 gRPC: Получен пустой аудиопоток!")
            return b""

        if len(pcm_data) % 2 != 0:
            pcm_data = pcm_data[:-1]

        log_info(f"TTS v3 gRPC: Успешно собрано {len(pcm_data)} байт ровного PCM!")
        return bytes(pcm_data)

    except Exception as e:
        log_error(f"Исключение TTS v3 gRPC: {str(e)}")
        return b""
