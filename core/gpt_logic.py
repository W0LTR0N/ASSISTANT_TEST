import asyncio
import aiohttp
import json
import re
import time
import config
try:
    from core.prompts import SYSTEM_PROMPT
except ImportError:
    from prompts import SYSTEM_PROMPT
from core.logger import log_info, log_error

session_histories = {}
session_timestamps = {}
_dialog_semaphore = asyncio.Semaphore(5)
_summary_semaphore = asyncio.Semaphore(2)
_http_session = None
HISTORY_LIMIT = 30
SESSION_TTL = 1800

FALLBACK_PHRASE = "Простите, я вас не расслышал, повторите, пожалуйста."

async def _get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=config.SUMMARY_GPT_TIMEOUT))
    return _http_session

async def close_gpt_session():
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()

def _truncate_message(content: str, max_len=200):
    return content[:max_len] + "..." if len(content) > max_len else content

def _push(session_id: str, role: str, content: str):
    h = session_histories.setdefault(session_id, [])
    h.append({"role": role, "content": content})
    session_timestamps[session_id] = time.time()
    if len(h) > HISTORY_LIMIT:
        del h[:len(h) - HISTORY_LIMIT]

def clean_gpt_reply(text: str) -> str:
    if not text:
        return ""
    for stop_word in ["Пользователь:", "Клиент:", "\nПользователь", "\nКлиент", "Пользователь :", "Клиент :"]:
        if stop_word in text:
            text = text.split(stop_word)[0]
    for name in ["Ассистент:", "Марина:", config.BOT_NAME + ":"]:
        text = text.replace(name, "")
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        return FALLBACK_PHRASE
    for ch in ["*", "#", "_", "`"]:
        text = text.replace(ch, "")
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln[:2] in ("- ", "• "):
            ln = ln[2:].strip()
        if ln:
            lines.append(ln)
    text = re.sub(r"\s{2,}", " ", " ".join(lines)).strip()
    if len(text) > 320:
        cut = text[:320]
        last_stop = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
        text = cut[:last_stop + 1] if last_stop > 50 else cut.rsplit(' ', 1)[0] + "."
    return text

async def _completion(messages: list, temperature: float, max_tokens: int,
                      sem: asyncio.Semaphore = None, timeout: float = None, session_id: str = None):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Api-Key {config.YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json",
    }
    # ВАЖНО: YandexGPT требует поле "text", а не "content"
    yandex_messages = [{"role": m["role"], "text": m["content"]} for m in messages]
    payload = {
        "modelUri": f"gpt://{config.YANDEX_FOLDER_ID}/{config.YANDEX_GPT_MODEL}",
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
        "messages": yandex_messages,
    }
    req_timeout = aiohttp.ClientTimeout(total=timeout or config.SUMMARY_GPT_TIMEOUT)
    try:
        async with (sem or _dialog_semaphore):
            session = await _get_http_session()
            async with session.post(url, headers=headers, json=payload, timeout=req_timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['result']['alternatives'][0]['message']['text']
                error_text = await response.text()
                log_error(f"[{session_id or '-'}] GPT ошибка {response.status}: {error_text}")
                return None
    except Exception as e:
        log_error(f"[{session_id or '-'}] Ошибка при обращении к GPT: {e}")
        return None

async def ask_yandex_gpt(user_input: str, session_id: str, system_override: str = None):
    _push(session_id, "user", _truncate_message(user_input))
    active_system_prompt = system_override if system_override else SYSTEM_PROMPT
    temperature = 0.2 if system_override else 0.3
    max_tokens = 400 if system_override else 200
    messages = [{"role": "system", "content": active_system_prompt}]
    messages += [dict(m) for m in session_histories.get(session_id, [])]
    t0 = time.monotonic()
    raw = await _completion(messages, temperature, max_tokens,
                            timeout=config.LIVE_GPT_TIMEOUT, session_id=session_id)
    log_info(f"[{session_id}] GPT latency: {time.monotonic() - t0:.2f}s")
    answer = clean_gpt_reply(raw) if raw else FALLBACK_PHRASE
    if session_id in session_histories:
        _push(session_id, "assistant", answer)
    return answer

def seed_greeting(session_id: str, greeting_text: str):
    _push(session_id, "assistant", greeting_text)

def get_session_history_formatted(session_id: str) -> list:
    return [
        {"role": ("client" if m["role"] == "user" else "manager"), "text": m["content"]}
        for m in session_histories.get(session_id, [])
    ]

# ===== Аналитика звонка для Albato =====
SUMMARY_SYSTEM = (
    "Ты аналитик звонков детейлинг-центра. По диалогу менеджера и клиента верни СТРОГО один JSON-объект, "
    "без markdown и без ```: "
    "{\"summary\": \"краткое содержание звонка 1-2 предложения\", "
    "\"client_name\": \"имя клиента или null, если не называл\", "
    "\"car_model\": \"марка и модель автомобиля или null, если не называл\", "
    "\"service\": \"услуга, которая интересует клиента (керамика, полировка, мойка, оклейка и т.п.) или null\", "
    "\"preferred_time\": \"желаемое время визита или перезвона или null\", "
    "\"intent\": \"запись|уточнение цен|консультация|другое\", "
    "\"notes\": \"прочие важные детали или null\"}"
)

SUMMARY_FIELDS = ["summary", "client_name", "car_model", "service", "preferred_time", "intent", "notes"]
_NULL_MARKERS = {"", "null", "none", "не указан", "не указано", "не называл", "нет", "-"}

def _parse_json_loose(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r'^```(?:json)?', '', text).rstrip('`').strip()
    data = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = {}
    if not data:
        data = {"summary": text[:300]}

    result = {}
    for field in SUMMARY_FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            value = value.strip()
            result[field] = value if value.lower() not in _NULL_MARKERS else None
        else:
            result[field] = None
    if not result["summary"]:
        result["summary"] = (text[:300] or "Не удалось проанализировать диалог.")
    return result

async def generate_call_summary(transcript: list, session_id: str = None) -> dict:
    if not transcript:
        return {f: None for f in SUMMARY_FIELDS} | {"summary": "Разговор не состоялся."}
    dialogue = "\n".join(f"{t['role']}: {t['text']}" for t in transcript)
    raw = await _completion(
        [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": dialogue}],
        0.1, 500, _summary_semaphore, timeout=config.SUMMARY_GPT_TIMEOUT, session_id=session_id,
    )
    if not raw:
        return {f: None for f in SUMMARY_FIELDS} | {"summary": "Не удалось проанализировать диалог."}
    return _parse_json_loose(raw)

def clear_session_context(session_id: str):
    session_histories.pop(session_id, None)
    session_timestamps.pop(session_id, None)

async def cleanup_old_sessions():
    log_info(f"Запущен фоновый клинер GPT-сессий (TTL={SESSION_TTL}с)")
    while True:
        await asyncio.sleep(600)
        now = time.time()
        old_sessions = [
            sid for sid, ts in session_timestamps.items()
            if now - ts > SESSION_TTL
        ]
        for sid in old_sessions:
            clear_session_context(sid)
            log_info(f"Удалена старая сессия {sid} (неактивна > {SESSION_TTL}с)")
