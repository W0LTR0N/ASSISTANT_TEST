import aiohttp
import base64
import json  # Обязательно добавь этот импорт
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

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
            {"voice": "alexander"},
            {"speed": 1.0}
        ]
    }

    timeout = aiohttp.ClientTimeout(total=5.0, connect=2.0)
    pcm_data = bytearray()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    # Читаем ответ построчно (потоком)
                    async for line in response.content:
                        if not line:
                            continue
                        try:
                            # Парсим каждый чанк отдельно
                            chunk_json = json.loads(line)
                            # Проверяем, есть ли аудио-данные в этом чанке
                            if "audioChunk" in chunk_json:
                                b64_data = chunk_json["audioChunk"].get("data", "")
                                if b64_data:
                                    pcm_data.extend(base64.b64decode(b64_data))
                        except Exception as e:
                            log_error(f"Ошибка при парсинге чанка TTS: {e}")
                            continue
                  
                    if len(pcm_data) == 0:
                        log_error("TTS v3: Получен пустой поток аудио")
                        return b""

                    # Выравниваем сэмплы
                    if len(pcm_data) % 2 != 0:
                        pcm_data = pcm_data[:-1]

                    log_info(f"TTS v3: Успешно собрано {len(pcm_data)} байт аудио")
                    return bytes(pcm_data)
                else:
                    err_text = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_text}")
                    return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {e}")
        return b""
