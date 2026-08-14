import aiohttp
import json
import os
from core.logger import log_info, log_error

# Берем ключи Яндекса из окружения
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")

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

# Хранилище контекста звонков в памяти
session_histories = {}

async def clear_session_context(session_id: str = "default"):
    """Очищает историю диалога для конкретной сессии звонка"""
    if session_id in session_histories:
        del session_histories[session_id]
        log_info(f"Контекст сессии {session_id} очищен")

async def get_session_history_formatted(session_id: str = "default"):
    """Возвращает форматированную историю сессии для sip_worker.py"""
    history = session_histories.get(session_id, [])
    return "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])

async def get_session_history(session_id: str = "default"):
    """Дубликат функции получения истории"""
    return session_histories.get(session_id, [])

async def ask_yandex_gpt(text: str, session_id: str = "default") -> str:
    """
    Главная функция, которую вызывает sip_worker.py (Работает напрямую с Yandex GPT)
    """
    if not text:
        return ""

    if session_id not in session_histories:
        session_histories[session_id] = []

    # Добавляем сообщение пользователя в историю
    session_histories[session_id].append({"role": "user", "content": text})

    # Ограничиваем историю последними 6 сообщениями
    recent_history = session_histories[session_id][-6:]

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [{"role": "system", "text": SYSTEM_PROMPT}]
    for msg in recent_history:
        messages.append({"role": msg["role"], "text": msg["content"]})

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": "100"
        },
        "messages": messages
    }

    timeout = aiohttp.ClientTimeout(total=4.0, connect=1.5)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    answer = data["result"]["alternatives"][0]["message"]["text"].strip()
                   
                    # Сохраняем ответ ассистента в историю
                    session_histories[session_id].append({"role": "assistant", "content": answer})
                    log_info(f"GPT [{session_id}]: {answer}")
                    return answer
                else:
                    err_text = await response.text()
                    log_error(f"GPT Ошибка [{response.status}]: {err_text}")
                    return "Я вас понял. Подскажите, на какой день вам удобнее записаться?"
    except Exception as e:
        log_error(f"Исключение GPT: {e}")
        return "Да, слушаю вас. Назовите, пожалуйста, марку вашего автомобиля."

async def get_gpt_response(history_messages):
    if isinstance(history_messages, str):
        return await ask_yandex_gpt(history_messages)
    return await ask_yandex_gpt(history_messages[-1]["content"] if history_messages else "")
