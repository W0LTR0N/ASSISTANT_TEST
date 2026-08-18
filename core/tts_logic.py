import os
import time
import wave
import audioop
import asyncio
import grpc
from google.protobuf.json_format import ParseDict
from yandex.cloud.ai.tts.v3 import tts_pb2, tts_service_pb2_grpc
from config import YANDEX_API_KEY, YANDEX_FOLDER_ID, YANDEX_TTS_VOICE, YANDEX_TTS_SPEED
from core.logger import log_info, log_error

BACKGROUND_FILE = "background_noise.wav"
BACKGROUND_PCM = b""
BACKGROUND_OK = False

def _load_background():
    if not os.path.exists(BACKGROUND_FILE):
        log_info("Файл фонового шума не найден, работаем без фона.")
        return b"", False
    try:
        with wave.open(BACKGROUND_FILE, 'rb') as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        if sample_width != 2:
            raw = audioop.lin2lin(raw, sample_width, 2)
        if n_channels == 2:
            raw = audioop.tomono(raw, 2, 0.5, 0.5)
        if frame_rate != 8000:
            raw, _ = audioop.ratecv(raw, 2, 1, frame_rate, 8000, None)
        log_info(f"Фоновый шум приведён к 8000Hz/mono/16bit ({len(raw)} байт).")
        return raw, True
    except Exception as wave_err:
        log_error(f"Ошибка загрузки {BACKGROUND_FILE}, фон отключён: {wave_err}")
        return b"", False

BACKGROUND_PCM, BACKGROUND_OK = _load_background()

def mix_background(speech_pcm: bytes, bg_pcm: bytes, bg_volume: float = 0.12, speech_volume: float = 0.88) -> bytes:
    """
    Peak normalization: снижаем громкость до сложения (88% + 12% = 100%).
    Если после сложения есть пики > ±30000, масштабируем весь сигнал вниз,
    чтобы гарантировать отсутствие клиппинга при lin2alaw.
    """
    if not BACKGROUND_OK or not bg_pcm or not speech_pcm:
        return speech_pcm
    try:
        speech_adj = audioop.mul(speech_pcm, 2, speech_volume)
        bg_adj = audioop.mul(bg_pcm, 2, bg_volume)
        speech_len = len(speech_adj)
        bg_len = len(bg_adj)
        if bg_len == 0:
            return speech_pcm
        if bg_len < speech_len:
            bg_adj = (bg_adj * ((speech_len // bg_len) + 1))[:speech_len]
        else:
            bg_adj = bg_adj[:speech_len]

        mixed = audioop.add(speech_adj, bg_adj, 2)

        try:
            max_val = audioop.max(mixed, 2)
            min_val = audioop.min(mixed, 2)
            peak = max(abs(max_val), abs(min_val))
            if peak > 30000:
                scale = 30000.0 / peak
                mixed = audioop.mul(mixed, 2, scale)
        except Exception:
            pass

        return mixed
    except Exception as e:
        log_error(f"Ошибка микширования: {e}")
        return speech_pcm

_GRPC_CHANNEL_OPTIONS = [
    ('grpc.keepalive_time_ms', 30000),
    ('grpc.keepalive_timeout_ms', 10000),
    ('grpc.keepalive_permit_without_calls', True),
    ('grpc.http2.max_pings_without_data', 0),
]

def create_tts_channel():
    credentials = grpc.ssl_channel_credentials()
    channel = grpc.aio.secure_channel('tts.api.cloud.yandex.net:443', credentials, options=_GRPC_CHANNEL_OPTIONS)
    stub = tts_service_pb2_grpc.SynthesizerStub(channel)
    return channel, stub

def _build_request(clean_text: str) -> tts_pb2.UtteranceSynthesisRequest:
    request_dict = {
        "text": clean_text,
        "outputAudioSpec": {"rawAudio": {"audioEncoding": "LINEAR16_PCM", "sampleRateHertz": 8000}},
        "hints": [{"voice": YANDEX_TTS_VOICE}],
    }
    if YANDEX_TTS_SPEED and abs(YANDEX_TTS_SPEED - 1.0) > 1e-6:
        try:
            d = dict(request_dict)
            d["hints"] = request_dict["hints"] + [{"speed": YANDEX_TTS_SPEED}]
            return ParseDict(d, tts_pb2.UtteranceSynthesisRequest())
        except Exception:
            log_error("TTS: hint speed не поддержан proto, синтезируем без speed.")
    return ParseDict(request_dict, tts_pb2.UtteranceSynthesisRequest())

async def _synthesize_stream(clean_text: str, stub, folder_id: str) -> bytes:
    req = _build_request(clean_text)
    metadata = (
        ('x-folder-id', folder_id),
        ('authorization', f'Api-Key {YANDEX_API_KEY}'),
    )
    speech_pcm = bytearray()
    try:
        stream = stub.UtteranceSynthesis(req, metadata=metadata)
        async for response in stream:
            if response.HasField("audio_chunk"):
                speech_pcm.extend(response.audio_chunk.data)
    except Exception as e:
        log_error(f"TTS stream ошибка: {e}")
        return b""
    return bytes(speech_pcm)

async def synthesize_speech_yandex(text, stub=None, folder_id=None, timeout: float = 8.0, session_id=None) -> bytes:
    clean_text = text.replace('*', '').replace('#', '').strip()
    if not clean_text:
        return b""
    if folder_id is None:
        folder_id = YANDEX_FOLDER_ID

    owns_channel = False
    channel = None
    t0 = time.monotonic()
    try:
        if stub is None:
            channel, stub = create_tts_channel()
            owns_channel = True
        raw_speech = await asyncio.wait_for(_synthesize_stream(clean_text, stub, folder_id), timeout=timeout)
        if not raw_speech:
            return b""
        result = mix_background(raw_speech, BACKGROUND_PCM)
        if session_id:
            log_info(f"[{session_id}] TTS latency: {time.monotonic() - t0:.2f}s")
        return result
    except asyncio.TimeoutError:
        log_error(f"[{session_id or '-'}] TTS: таймаут синтеза после {timeout}с, текст: '{clean_text[:60]}...'")
        return b""
    except Exception as e:
        log_error(f"[{session_id or '-'}] Исключение TTS v3 gRPC: {e}")
        return b""
    finally:
        if owns_channel and channel is not None:
            try:
                await channel.close()
            except Exception:
                pass
