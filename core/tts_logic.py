import aiohttp
import asyncio
import base64
import json
import logging
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

logger = logging.getLogger(__name__)

async def synthesize_speech_yandex(text: str) -> bytes:
    if not text:
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

    # Передаем containerAudioSpec WAV 8000 Hz для REST v3
    payload = {
        "text": clean_text,
        "hints": [{"voice": "alexander"}],
        "outputAudioSpec": {
            "containerAudioSpec": {
                "format": "WAV"
            }
        }
    }

    timeout = aiohttp.ClientTimeout(total=20.0, connect=5.0)
    audio_data = bytearray()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    log_info("TTS v3: Получен статус 200 OK, читаем поток...")
                   
                    async for chunk in response.content.iter_any():
                        if not chunk:
                            continue
                       
                        # Парсим NDJSON
                        lines = chunk.decode("utf-8", errors="ignore").splitlines()
                        for line in lines:
                            if not line.strip():
                                continue
                            try:
                                chunk_json = json.loads(line)
                                if "audioChunk" in chunk_json:
                                    b64_data = chunk_json["audioChunk"].get("data", "")
                                    if b64_data:
                                        audio_data.extend(base64.b64decode(b64_data))
                            except Exception:
                                continue

                    if len(audio_data) == 0:
                        log_error("TTS v3: Поток завершен, но данные пустые")
                        return b""

                    # Отрезаем WAV заголовок (44 байта), если он есть
                    pcm_payload = bytes(audio_data)
                    if len(pcm_payload) > 44 and pcm_payload[:4] == b'RIFF':
                        pcm_payload = pcm_payload[44:]

                    # Выравнивание по 2 байта
                    if len(pcm_payload) % 2 != 0:
                        pcm_payload = pcm_payload[:-1]

                    log_info(f"TTS v3: Успешно собрано {len(pcm_payload)} байт PCM")
                    return pcm_payload
               
                else:
                    err_body = await response.text()
                    log_error(f"TTS v3 Ошибка [{response.status}]: {err_body}")
                    return b""

    except Exception as e:
        log_error(f"Исключение TTS v3: {str(e)}")
        return b""
