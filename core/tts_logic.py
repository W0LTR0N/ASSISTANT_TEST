import aiohttp
import asyncio
import base64
import json
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
        return b""

    clean_text = text[:250]
    url = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
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
            "pcmAudioSpec": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        }
    }

    timeout = aiohttp.ClientTimeout(total=15.0, connect=5.0)
    pcm_data = bytearray()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    raw_response = await response.read()
                   
                    if not raw_response:
                        log_error("TTS v3: API вернуло 200 OK, но тело ответа ПУСТОЕ")
                        return b""
                   
                    # Разбиваем ответ по строкам (NDJSON формат)
                    lines = raw_response.decode("utf-8", errors="ignore").splitlines()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            chunk_json = json.loads(line)
                            if "audioChunk" in chunk_json:
                                b64_data = chunk_json["audioChunk"].get("data", "")
                                if b64_data:
                                    pcm_data.extend(base64.b64decode(b64_data))
                            elif "error" in chunk_json:
                                log_error(f"TTS v3 ОШИБКА в ответе: {chunk_json['error']}")
                            else:
                                log_info(f"TTS v3: Получен ответ без audioChunk: {line[:100]}...")
                        except Exception:
                            continue
                   
                    if len(pcm_data) == 0:
                        log_error("TTS v3: Данные получены, но audioChunk внутри НЕТ. Полный сырой ответ:")
                        log_error(raw_response.decode("utf-8", errors="ignore")[:500])
                        return b""

                    # Выравнивание по 2 байта для 16-bit PCM
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
