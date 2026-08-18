import asyncio
import json
import time
import aiohttp
import config
from core.logger import log_info, log_error

_albato_session = None
_send_lock = asyncio.Lock()


async def _get_session():
    global _albato_session
    if _albato_session is None or _albato_session.closed:
        _albato_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0))
    return _albato_session


async def close_albato_session():
    global _albato_session
    if _albato_session is not None and not _albato_session.closed:
        await _albato_session.close()


def _clean(value) -> str:
    """None -> пустая строка (Albato не любит null), остальное -> строка."""
    if value is None:
        return ""
    return str(value)


async def _post_lead(payload: dict) -> bool:
    headers = {"Content-Type": "application/json"}
    if config.WEBHOOK_SECRET and config.WEBHOOK_SECRET != "default_secret":
        headers["X-Webhook-Secret"] = config.WEBHOOK_SECRET
    try:
        session = await _get_session()
        async with session.post(config.ALBATO_WEBHOOK_URL, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status in (200, 201, 202, 204):
                return True
            log_error(f"Albato вернул статус {resp.status}: {text[:300]}")
            return False
    except Exception as e:
        log_error(f"Ошибка отправки лида в Albato: {e}")
        return False


def _save_failed_lead(payload: dict):
    try:
        with open(config.FAILED_LEADS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        log_error("Лид сохранён в failed_leads.log для повторной отправки.")
    except Exception as e:
        log_error(f"Не удалось сохранить неудачный лид: {e}")


async def send_lead_to_albato(phone: str, summary: str, transcript: list,
                              session_id: str, details: dict):
    """
    Отправляет лид в Albato. Поля плоские — так Albato легко маппит их на CRM.
    """
    details = details or {}
    payload = {
        # обязательная база
        "phone": _clean(phone),
        "summary": _clean(summary),
        "transcript": transcript,
        "session_id": _clean(session_id),
        "timestamp": int(time.time()),
        # главное для CRM
        "client_name": _clean(details.get("client_name")),
        "car_model": _clean(details.get("car_model")),
        "service": _clean(details.get("service")),
        "preferred_time": _clean(details.get("preferred_time")),
        "intent": _clean(details.get("intent")),
        "notes": _clean(details.get("notes")),
    }
    async with _send_lock:
        ok = await _post_lead(payload)
        if ok:
            log_info(f"[{session_id}] Лид успешно отправлен в Albato для {phone}")
        else:
            _save_failed_lead(payload)


async def resend_failed_leads():
    """При старте доотправляет лиды, которые не ушли в прошлый раз."""
    try:
        with open(config.FAILED_LEADS_FILE, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return
    except Exception as e:
        log_error(f"Не удалось прочитать failed_leads.log: {e}")
        return

    if not lines:
        return

    log_info(f"Найдено {len(lines)} неотправленных лидов, повторяем отправку...")
    remaining = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        ok = await _post_lead(payload)
        if ok:
            log_info(f"Повторно отправлен старый лид для {payload.get('phone')}")
        else:
            remaining.append(line)
        await asyncio.sleep(1)

    try:
        with open(config.FAILED_LEADS_FILE, "w", encoding="utf-8") as f:
            for line in remaining:
                f.write(line + "\n")
    except Exception as e:
        log_error(f"Не удалось перезаписать failed_leads.log: {e}")
