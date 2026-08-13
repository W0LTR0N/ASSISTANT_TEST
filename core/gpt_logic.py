import re
import aiohttp
from config import YANDEX_GPT_API_KEY, YANDEX_FOLDER_ID, YANDEX_GPT_MODEL
from core.logger import log_info, log_error
from prompts import SYSTEM_PROMPT

CONVERSATION_SESSIONS = {}

def clean_text_for_tts(text: str) -> str:
    # Жестко вырезаем названия ролей, если GPT их случайно сгенерировал
    text = re.sub(r'^(Пользователь|Ассистент|Бот|Клиент|Оператор):\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'(Пользователь|Ассистент|Бот|Клиент|Оператор):', '', text, flags=re.IGNORECASE)
    # Убираем спецсимволы и лишние пробелы
    text = re.sub(r'[*_~`#\-+\[\]()"\']', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def clear_session_context(session_id: str):
    if session_id in CONVERSATION_SESSIONS:
        del CONVERSATION_SESSIONS[session_id]
        log_info(f"Контекст сессии очищен: {session_id}")

def get_session_history_formatted(session_id: str) -> str:
    history = CONVERSATION_SESSIONS.get(session_id, [])
    lines = []
    for m in history:
        role = "Клиент" if m["role"] == "user" else "Бот"
        lines.append(f"{role}: {m['text']}")
    return "\n".join(lines)

async def ask_yandex_gpt(user_message: str, session_id: str = "default") -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}"
    }

    if session_id not in CONVERSATION_SESSIONS:
        CONVERSATION_SESSIONS[session_id] = []

    history = CONVERSATION_SESSIONS[session_id]
  
    messages = [{"role": "system", "text": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "text": user_message})

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{YANDEX_GPT_MODEL}",
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,
            "maxTokens": 90
        },
        "messages": messages
    }

    timeout = aiohttp.ClientTimeout(total=3.5, connect=1.0)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status != 200:
                    log_error(f"GPT Ошибка [{response.status}]")
                    return "Извините, плохо вас слышно. Повторите, пожалуйста."
            
                result = await response.json()
                alternatives = result.get("result", {}).get("alternatives", [])
                if not alternatives:
                    return "Повторите, пожалуйста."
            
                raw_reply = alternatives[0].get("message", {}).get("text", "")
                bot_reply = clean_text_for_tts(raw_reply)
              
                history.append({"role": "user", "text": user_message})
                history.append({"role": "assistant", "text": bot_reply})
              
                log_info(f"GPT [{session_id}]: {bot_reply}")
                return bot_reply

    except Exception as e:
        log_error(f"Исключение GPT: {e}")
        return "Вас плохо слышно, повторите ещё раз."
