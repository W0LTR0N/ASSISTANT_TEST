SYSTEM_PROMPT = """
Ты — вежливый и профессиональный голосовой ИИ-ассистент компании Woltron.
Твоя задача — отвечать на вопросы клиента по телефону, консультировать по услугам и фиксировать заявку.

Правила общения:
1. Отвечай кратко, максимум 1-2 предложения (не более 20-25 слов).
2. Никогда не используй спецсимволы, звездочки (*), дефисы, эмодзи или разметку Markdown.
3. Пиши все числа словами (например, "двадцать пять", а не "25").
4. Говори естественно, вежливо и четко, поддерживая живой диалог.
"""

Кор логгер
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("woltron_bot")

def log_info(msg: str):
    logger.info(msg)

def log_error(msg: str):
    logger.error(msg)

Core/sst_logic.py

import aiohttp
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error

async def transcribe_audio_yandex(pcm_bytes: bytes) -> str:
    if not pcm_bytes or len(pcm_bytes) < 1600:
        return ""

    url = (
        f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        f"?topic=general&folderId={YANDEX_FOLDER_ID}&lang=ru-RU"
        f"&format=lpcm&sampleRateHertz=8000"
    )
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}"
    }

    timeout = aiohttp.ClientTimeout(total=4.0, connect=1.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=pcm_bytes) as response:
                if response.status == 200:
                    result = await response.json()
                    text = result.get("result", "").strip()
                    if text:
                        log_info(f"STT Распознано: '{text}'")
                    return text
                else:
                    err_text = await response.text()
                    log_error(f"STT Ошибка [{response.status}]: {err_text}")
                    return ""
    except Exception as e:
        log_error(f"Исключение STT: {e}")
        return ""