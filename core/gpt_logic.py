
import aiohttp
import json
import os
from config import YANDEX_API_KEY, YANDEX_FOLDER_ID
from core.logger import log_info, log_error
from prompts import SYSTEM_PROMPT

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
    """Возвращает сырой массив истории"""
    return session_histories.get(session_id, [])

async def ask_yandex_gpt(text: str, session_id: str = "default", system_override: str = None) -> str:
    """
    Главная функция, которую вызывает sip_worker.py (Работает напрямую с Yandex GPT)
    """
    if not text:
        return ""

    if session_id not in session_histories and not system_override:
        session_histories[session_id] = []

    # Добавляем сообщение в историю только если это диалог, а не служебный парсер
    if not system_override:
        session_histories[session_id].append({"role": "user", "content": text})
        recent_history = session_histories[session_id][-6:]
    else:
        recent_history = [{"role": "user", "content": text}]

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "Content-Type": "application/json"
    }

    active_system_prompt = system_override if system_override else SYSTEM_PROMPT

    messages = [{"role": "system", "text": active_system_prompt}]
    for msg in recent_history:
        messages.append({"role": msg["role"], "text": msg["content"]})

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.2 if system_override else 0.3,
            "maxTokens": "150"
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
                   
                    if not system_override:
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
