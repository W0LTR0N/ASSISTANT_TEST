import os
import wave
import audioop
import logging
import grpc
from google.protobuf.json_format import ParseDict
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc
from config import YANDEX_API_KEY, YANDEX_FOLDER_ID

logger = logging.getLogger("woltron")

BACKGROUND_FILE = "background_noise.wav"
BACKGROUND_PCM = b""

if os.path.exists(BACKGROUND_FILE):
    try:
        with wave.open(BACKGROUND_FILE, 'rb') as wf:
            BACKGROUND_PCM = wf.readframes(wf.getnframes())
            logger.info(f"Фоновый шум загружен через wave ({len(BACKGROUND_PCM)} байт).")
    except Exception as wave_err:
        try:
            with open(BACKGROUND_FILE, 'rb') as f:
                BACKGROUND_PCM = f.read()
            logger.info(f"Фоновый шум загружен как raw PCM ({len(BACKGROUND_PCM)} байт).")
        except Exception as raw_err:
            logger.error(f"Ошибка загрузки {BACKGROUND_FILE}: {raw_err}")
            BACKGROUND_PCM = b""

def mix_background(speech_pcm: bytes, bg_pcm: bytes, bg_volume: float = 0.25) -> bytes:
    if not bg_pcm or not speech_pcm:
        return speech_pcm

    try:
        adjusted_bg = audioop.mul(bg_pcm, 2, bg_volume)
        speech_len = len(speech_pcm)
        bg_len = len(adjusted_bg)
       
        if bg_len < speech_len:
            repeats = (speech_len // bg_len) + 1
            adjusted_bg = (adjusted_bg * repeats)[:speech_len]
        else:
            adjusted_bg = adjusted_bg[:speech_len]

        return audioop.add(speech_pcm, adjusted_bg, 2)
    except Exception as e:
        logger.error(f"Ошибка микширования: {e}")
        return speech_pcm

async def synthesize_speech_yandex(
    text: str,
    stub: tts_service_pb2_grpc.SynthesizerStub = None,
    folder_id: str = None
) -> bytes:
    clean_text = text.replace('*', '').replace('#', '').replace('-', ' ').strip()
    if not clean_text:
        return b""

    if folder_id is None:
        folder_id = YANDEX_FOLDER_ID

    # Строго 1 поле внутри элемента массива hints (так как в protobuf это oneof)
    request_dict = {
        "text": clean_text,
        "outputAudioSpec": {
            "rawAudio": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        },
        "hints": [
            {
                "voice": "filipp"
            }
        ]
    }

    try:
        if stub is None:
            credentials = grpc.ssl_channel_credentials()
            channel = grpc.aio.secure_channel('tts.api.cloud.yandex.net:443', credentials)
            stub = tts_service_pb2_grpc.SynthesizerStub(channel)

        req = ParseDict(request_dict, tts_pb2.UtteranceSynthesisRequest())
        metadata = (
            ('x-folder-id', folder_id),
            ('authorization', f'Api-Key {YANDEX_API_KEY}'),
        )

        speech_pcm = bytearray()
        stream = stub.UtteranceSynthesis(req, metadata=metadata)
        async for response in stream:
            if response.HasField("audio_chunk"):
                speech_pcm.extend(response.audio_chunk.data)

        raw_speech = bytes(speech_pcm)
        if not raw_speech:
            return b""

        return mix_background(raw_speech, BACKGROUND_PCM, bg_volume=0.25)

    except Exception as e:
        logger.error(f"Исключение TTS v3 gRPC: {e}")
        return b""
