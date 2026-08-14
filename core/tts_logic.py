import aiohttp
import asyncio
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    log_info(f"TTS: Запрос синтеза v3 для текста: {clean_text[:50]}...")

    url = "https://tts.api.cloud.yandex.net/tts/v3/synthesis"
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json"
    }
    if YANDEX_FOLDER_ID:
        headers["x-folder-id"] = YANDEX_FOLDER_ID

    payload = {
        "text": clean_text,
        "hints": [{"voice": "alexander"}],
        "outputAudioSpec": {
            "containerAudioSpec": {
                "format": "WAV"
            }
        }
    }

    timeout = aiohttp.ClientTimeout(total=15.0, connect=5.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    wav_data = await response.read()
                   
                    if not wav_data:
                        log_error("TTS v3: Получен пустой ответ от сервера")
                        return b""

                    # Срезаем 44 байта WAV-заголовка, получаем чистый PCM
                    pcm_data = wav_data[44:] if len(wav_data) > 44 and wav_data[:4] == b'RIFF' else wav_data

                    # Выравнивание под 16-bit PCM (2 байта на сэмпл)
                    if len(pcm_data) % 2 != 0:
                        pcm_data = pcm_data[:-1]

                    log_info(f"TTS v3: Успешно получено {len(pcm_data)} байт чистейшего PCM")
                    return pcm_data
                else:
                    err_text = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_text}")
                    return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {e}")
        return b""
