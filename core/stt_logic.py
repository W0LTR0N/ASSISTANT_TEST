import time
import aiohttp
import config
from core.logger import log_info, log_error

_stt_session = None

async def _get_stt_session():
    global _stt_session
    if _stt_session is None or _stt_session.closed:
        _stt_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
    return _stt_session

async def transcribe_audio_yandex(pcm_data: bytes, session_id: str) -> str:
    if not pcm_data:
        return ""
    url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={config.YANDEX_FOLDER_ID}&lang=ru-RU"
    headers = {
        "Authorization": f"Api-Key {config.YANDEX_API_KEY}",
        "Content-Type": "audio/x-pcm;bit=16;rate=8000",
    }
    t0 = time.monotonic()
    try:
        session = await _get_stt_session()
        async with session.post(url, headers=headers, data=pcm_data) as resp:
            dt = time.monotonic() - t0
            if resp.status == 200:
                result = await resp.json()
                text = result.get("result", "").strip()
                if text:
                    log_info(f"[{session_id}] STT Распознано: '{text}'")
                    log_info(f"[{session_id}] STT latency: {dt:.2f}s")
                return text
            error_text = await resp.text()
            log_error(f"[{session_id}] STT ошибка {resp.status} ({dt:.2f}s): {error_text}")
            return ""
    except Exception as e:
        log_error(f"[{session_id}] STT вызов упал ({time.monotonic() - t0:.2f}s): {e}")
        return ""

async def close_stt_session():
    global _stt_session
    if _stt_session is not None and not _stt_session.closed:
        await _stt_session.close()
