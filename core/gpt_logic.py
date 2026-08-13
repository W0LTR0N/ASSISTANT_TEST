import aiohttp
import json
import os
from config import OPENAI_API_KEY  # Если используется VseGPT или OpenAI
from core.logger import log_info, log_error

# Если ключа OpenAI нет в config, проверяем системные переменные
API_KEY = OPENAI_API_KEY if 'OPENAI_API_KEY' in globals() else os.getenv("OPENAI_API_KEY", "")

# Жесткий армейский промпт, лечащий галлюцинации и болтливость
SYSTEM_PROMPT = """
Ты — профессиональный голос-менеджер детейлинг-центра Woltron.
Твоя главная цель: вежливо, четко и коротко проконсультировать клиента и записать его на услугу (полировка, оклейка, керамика).

ЖЕСТКИЕ ПРАВИЛА ОБЩЕНИЯ:
1. Отвечай СТРОГО 1-2 короткими предложениями. Говори максимально естественно для телефона.
2. НИКОГДА не отвечай за клиента, не придумывай продолжение разговора за него.
3. НИКОГДА не выдумывай марку машины клиента (не говори "У меня Тойота" или "Ваша БМВ", пока клиент сам не назовет авто).
4. Задавай ровно один вопрос за раз, чтобы продвигать запись.
5. Если спрашивают цену — назови примерный диапазон и предложи записаться на бесплатный осмотр.
"""

async def get_gpt_response(history_messages: list) -> str:
    """
    Принимает историю диалога формата [{'role': 'user', 'content': '...'}, ...],
    отправляет запрос в GPT и возвращает короткий ответ.
    """
    if not API_KEY:
        log_error("GPT Error: API_KEY не найден в конфиге или env")
        return "Извините, сейчас есть технические накладки со связью. Чем могу помочь?"

    url = "https://api.vsegpt.ru/v1/chat/completions" # Либо https://api.openai.com/v1/chat/completions

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # Формируем полный список сообщений с системной инструкцией вначале
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
   
    # Добавляем накопившуюся историю диалога
    if isinstance(history_messages, list):
        messages.extend(history_messages)
    elif isinstance(history_messages, str):
        messages.append({"role": "user", "content": history_messages})

    payload = {
        "model": "openai/gpt-3.5-turbo", # Или используемая у тебя модель (например, openai/gpt-4o-mini)
        "messages": messages,
        "temperature": 0.3, # Низкая температура, чтобы бот не фантазировал
        "max_tokens": 100    # Жесткое ограничение длины, чтобы ответ был быстрым и коротким
    }

    timeout = aiohttp.ClientTimeout(total=4.0, connect=1.5)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    answer = data["choices"][0]["message"]["content"].strip()
                    log_info(f"GPT Успешный ответ: {answer}")
                    return answer
                else:
                    err_text = await response.text()
                    log_error(f"GPT Ошибка [{response.status}]: {err_text}")
                    return "Я вас понял. Подскажите, на какой день вам удобнее записаться?"
    except Exception as e:
        log_error(f"Исключение при запросе к GPT: {e}")
        return "Да, слушаю вас. Назовите, пожалуйста, марку вашего автомобиля."
