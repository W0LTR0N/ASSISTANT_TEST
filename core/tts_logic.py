import aiohttp
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}"
    }
    data = {
        "text": text,
        "lang": "ru-RU",
        "voice": "alena",
        "format": "lpcm",
        "sampleRateHertz": "8000",
        "folderId": YANDEX_FOLDER_ID
    }

    timeout = aiohttp.ClientTimeout(total=4.0, connect=1.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=data) as response:
                if response.status == 200:
                    pcm_data = await response.read()
                    log_info(f"TTS Синтезировано PCM байт: {len(pcm_data)}")
                    return pcm_data
                else:
                    err_text = await response.text()
                    log_error(f"TTS Ошибка [{response.status}]: {err_text}")
                    return b""
    except Exception as e:
        log_error(f"Исключение TTS: {e}")
        return b""
