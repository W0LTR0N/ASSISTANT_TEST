import aiohttp
import asyncio
import base64
import json
import logging
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

# Настройка базового логгера, если нужен специфичный уровень
logger = logging.getLogger(__name__)

async def synthesize_speech_yandex(text: str) -> bytes:
    """
    Финальная версия TTS Yandex v3 с полной отладкой.
    Синтезирует речь, обрабатывает потоковый NDJSON и логирует каждый шаг.
    """
    if not text:
        log_info("TTS: Пустой текст для синтеза")
        return b""

    clean_text = text[:250]
    log_info(f"TTS: Запрос синтеза для текста: {clean_text[:50]}...")

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

    timeout = aiohttp.ClientTimeout(total=20.0, connect=5.0)
    pcm_data = bytearray()
    raw_buffer = bytearray()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    log_info("TTS v3: Получен статус 200 OK, начинаем чтение потока...")
                   
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
                                        log_info(f"TTS v3: Получен чанк данных, накоплено {len(pcm_data)} байт")
                            except Exception as parse_err:
                                log_error(f"TTS v3: Ошибка парсинга чанка: {parse_err}")
                                continue

                    # Проверка результата
                    if len(pcm_data) == 0:
                        log_error("TTS v3: Поток завершен, но данные аудио пусты!")
                        return b""

                    # Выравнивание по 2 байта (16-bit PCM)
                    if len(pcm_data) % 2 != 0:
                        pcm_data = pcm_data[:-1]

                    log_info(f"TTS v3: Успешно собрано {len(pcm_data)} байт аудио")
                    return bytes(pcm_data)
               
                else:
                    err_body = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_body}")
                    return b""

    except asyncio.TimeoutError:
        log_error("TTS v3: Таймаут ожидания Yandex API")
        return b""
    except Exception as e:
        log_error(f"Исключение TTS v3: {str(e)}")
        return b""
