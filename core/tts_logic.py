import os
import re
import logging
import grpc
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc

logger = logging.getLogger(__name__)

IAM_TOKEN = os.getenv("YANDEX_IAM_TOKEN")
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

def split_text_into_chunks(text: str, max_chars: int = 200) -> list[str]:
    """Разбивает длинный текст по предложениям или словам, чтобы уложиться в лимит Yandex TTS v3."""
    if len(text) <= max_chars:
        return [text]
   
    # Разбиваем по знакам препинания
    sentences = re.split(r'(?<=[.!?]) +', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            current_chunk = (current_chunk + " " + sentence).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence[:max_chars]

    if current_chunk:
        chunks.append(current_chunk)
       
    return chunks

def synthesize_speech_v3(text: str) -> bytes:
    """Синтезирует речь через Yandex SpeechKit v3 gRPC и возвращает сырой PCM (24kHz, 16bit, mono)."""
    if not text or not text.strip():
        return b""

    chunks = split_text_into_chunks(text, max_chars=200)
    full_audio = bytearray()

    try:
        cred = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel('tts.api.cloud.yandex.net:443', cred)
        stub = tts_service_pb2_grpc.SynthesizerStub(channel)

        for chunk in chunks:
            request = tts_pb2.UtteranceSynthesisRequest(
                text=chunk,
                output_audio_spec=tts_pb2.AudioFormatOptions(
                    container_audio=tts_pb2.ContainerAudio(
                        container_audio_type=tts_pb2.ContainerAudio.RAW
                    )
                ),
                hints=[
                    tts_pb2.Hints(voice="filipp"),
                    tts_pb2.Hints(speed=1.0)
                ],
                loudness_normalization_type=tts_pb2.UtteranceSynthesisRequest.LUFS
            )

            metadata = (
                ('authorization', f'Bearer {IAM_TOKEN}'),
                ('x-folder-id', FOLDER_ID)
            )

            response_stream = stub.UtteranceSynthesis(request, metadata=metadata)
           
            for response in response_stream:
                if response.HasField('audio_chunk'):
                    full_audio.extend(response.audio_chunk.data)

        return bytes(full_audio)

    except Exception as e:
        logger.error(f"Исключение TTS v3 gRPC: {e}")
        return b""
