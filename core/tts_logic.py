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

    # В v3 передаем СТРОГО audioEncoding и убираем конфликтный hints
    payload = {
        "text": clean_text,
        "outputAudioSpec": {
            "pcmAudioSpec": {
                "audioEncoding": "LINEAR16_PCM",
                "sampleRateHertz": 8000
            }
        }
    }

    timeout = aiohttp.ClientTimeout(total=10.0, connect=3.0)
    pcm_data = bytearray()
    raw_buffer = bytearray()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    async for chunk in response.content.iter_any():
                        if not chunk:
                            continue
                      
                        raw_buffer.extend(chunk)

                        while b"\n" in raw_buffer:
                            line, raw_buffer = raw_buffer.split(b"\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                chunk_json = json.loads(line.decode("utf-8", errors="ignore"))
                              
                                if "error" in chunk_json:
                                    log_error(f"TTS v3 ошибка сервера: {chunk_json['error']}")
                                    continue

                                if "audioChunk" in chunk_json:
                                    b64_data = chunk_json["audioChunk"].get("data", "")
                                    if b64_data:
                                        pcm_data.extend(base64.b64decode(b64_data))
                            except Exception as parse_err:
                                log_error(f"Ошибка парсинга чанка TTS: {parse_err}")
                                continue

                    # Добираем остаток буфера
                    if raw_buffer.strip():
                        try:
                            chunk_json = json.loads(raw_buffer.decode("utf-8", errors="ignore"))
                            if "audioChunk" in chunk_json:
                                b64_data = chunk_json["audioChunk"].get("data", "")
                                if b64_data:
                                    pcm_data.extend(base64.b64decode(b64_data))
                        except Exception as parse_err:
                            log_error(f"Ошибка парсинга остатка буфера TTS: {parse_err}")

                    if len(pcm_data) == 0:
                        log_error("TTS v3: Получен пустой поток аудио от API Яндекса")
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
    except asyncio.TimeoutError:
        log_error("TTS v3: Таймаут ожидания ответа от Yandex API")
        return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {e}")
        return b""
