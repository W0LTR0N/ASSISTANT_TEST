import aiohttp
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
        "x-folder-id": YANDEX_FOLDER_ID,
        "Content-Type": "application/json"
    }

    # Для сырого PCM 8000 Гц в v3 передается СТРОГО pcmAudioSpec
    payload = {
        "text": clean_text,
        "outputAudioSpec": {
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

                    if len(pcm_data) == 0:
                        log_error("TTS v3: Получен пустой поток аудио")
                        return b""

                    # Выравниваем сэмплы (16-bit PCM)
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
