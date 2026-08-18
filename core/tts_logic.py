import os
import time
import wave
import audioop
import asyncio
import aiohttp
import miniaudio
from config import (
    GENVOICE_API_KEY, GENVOICE_VOICE_ID, GENVOICE_API_URL, GENVOICE_OUTPUT_FORMAT,
)
from core.logger import log_info, log_error

BACKGROUND_FILE = "background_noise.wav"
BACKGROUND_PCM = b""
BACKGROUND_OK = False

_genvoice_session = None


async def _get_genvoice_session():
    global _genvoice_session
    if _genvoice_session is None or _genvoice_session.closed:
        _genvoice_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0))
    return _genvoice_session


async def close_genvoice_session():
    global _genvoice_session
    if _genvoice_session is not None and not _genvoice_session.closed:
        await _genvoice_session.close()


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


def mix_background(speech_pcm: bytes, bg_pcm: bytes, bg_volume: float = 0.05, speech_volume: float = 0.95) -> bytes:
    """Пик-нормализация: 95% речь + 5% фон, если пики > 30000 — масштабируем вниз."""
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


def _decode_to_pcm8k(audio_bytes: bytes) -> bytes:
    """Декодирует wav/mp3/ogg в raw PCM 8000 Hz 16-bit mono через miniaudio."""
    decoded = miniaudio.decode(
        audio_bytes,
        nchannels=1,
        sample_rate=8000,
        output_format=miniaudio.SampleFormat.SIGNED16,
    )
    return bytes(decoded.samples)


async def _synthesize_genvoice(clean_text: str) -> bytes:
    """Дёргает GenVoice API, возвращает сырые байты аудио (формат из конфига)."""
    headers = {
        "Authorization": f"Bearer {GENVOICE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "voice_id": GENVOICE_VOICE_ID,
        "text": clean_text,
        "output_format": GENVOICE_OUTPUT_FORMAT,
    }
    session = await _get_genvoice_session()
    async with session.post(GENVOICE_API_URL, headers=headers, json=payload) as resp:
        if resp.status in (200, 201):
            return await resp.read()
        error_text = await resp.text()
        log_error(f"GenVoice API ошибка {resp.status}: {error_text}")
        return b""


async def synthesize_speech(text, session_id=None, timeout: float = 15.0) -> bytes:
    """Возвращает PCM 8000 Hz 16-bit mono — формат для RTP-отправки."""
    clean_text = text.replace('*', '').replace('#', '').strip()
    if not clean_text:
        return b""

    t0 = time.monotonic()
    try:
        audio_bytes = await asyncio.wait_for(_synthesize_genvoice(clean_text), timeout=timeout)
        if not audio_bytes:
            return b""

        pcm = await asyncio.to_thread(_decode_to_pcm8k, audio_bytes)
        if not pcm:
            return b""

        result = mix_background(pcm, BACKGROUND_PCM)
        if session_id:
            log_info(f"[{session_id}] TTS latency (GenVoice): {time.monotonic() - t0:.2f}s")
        return result

    except asyncio.TimeoutError:
        log_error(f"[{session_id or '-'}] GenVoice: таймаут синтеза после {timeout}с, текст: '{clean_text[:60]}...'")
        return b""
    except Exception as e:
        log_error(f"[{session_id or '-'}] GenVoice ошибка: {e}")
        return b""
