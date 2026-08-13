import aiohttp
import base64
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    # Режем фразы до 250 символов, чтобы v3 не выплевывал ошибку 400
    clean_text = text[:250]

    url = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "x-folder-id": YANDEX_FOLDER_ID,
        "Content-Type": "application/json"
    }
   
    payload = {
        "text": clean_text,
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
                    res_json = await response.json()
                    audio_base64 = res_json.get("audioChunk", {}).get("data", "")
                   
                    if not audio_base64:
                        log_error("TTS v3: Пустой audioChunk в ответе")
                        return b""
                       
                    # Декодируем base64 в чистый PCM-звук
                    pcm_data = base64.b64decode(audio_base64)
                   
                    # Выравниваем сэмплы
                    if len(pcm_data) % 2 != 0:
                        pcm_data = pcm_data[:-1]

                    log_info(f"TTS v3 Декодировано PCM байт: {len(pcm_data)}")
                    return pcm_data
                else:
                    err_text = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_text}")
                    return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {e}")
        return b""
