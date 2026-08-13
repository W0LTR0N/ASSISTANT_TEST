import aiohttp
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    # Строго API v3 Neural
    url = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "x-folder-id": YANDEX_FOLDER_ID,
        "Content-Type": "application/json"
    }
   
    # Используем разрешенный голос alexander на движке v3
    payload = {
        "text": text,
        "outputAudioSpec": {
            "containerAudioSpec": {
                "containerAudioType": "RAW"
            },
            "pcmAudioSpec": {
                "sampleRateHertz": 8000
            }
        },
        "hints": [
            {
                "voice": "alexander"
            },
            {
                "speed": 1.0
            }
        ]
    }

    timeout = aiohttp.ClientTimeout(total=3.5, connect=1.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    pcm_data = await response.read()
                    log_info(f"TTS v3 Живой голос синтезирован PCM байт: {len(pcm_data)}")
                    return pcm_data
                else:
                    err_text = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_text}")
                    return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {e}")
        return b""
