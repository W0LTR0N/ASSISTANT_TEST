import aiohttp
import aiofiles
import asyncio
import json
import os
from datetime import datetime
from config import ALBATO_WEBHOOK_URL, FAILED_LEADS_FILE
from core.logger import log_info, log_error

_albato_session = None
_file_lock = asyncio.Lock()

async def _get_albato_session():
    global _albato_session
    if _albato_session is None or _albato_session.closed:
        _albato_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
    return _albato_session

async def close_albato_session():
    global _albato_session
    if _albato_session is not None and not _albato_session.closed:
        await _albato_session.close()

async def _save_failed_lead_locally(payload: dict):
    """asyncio.Lock + aiofiles: защита от повреждения JSONL при concurrent writes."""
    async with _file_lock:
        try:
            async with aiofiles.open(FAILED_LEADS_FILE, "a", encoding="utf-8") as f:
                await f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                await f.flush()
        except Exception as e:
            log_error(f"Не удалось сохранить лид даже локально: {e}")
    log_info(f"Лид сохранён локально в {FAILED_LEADS_FILE} (Albato была недоступна)")

async def send_lead_to_albato(phone: str, summary: str, transcript: list, session_id: str, details: dict = None):
    payload = {
        "lead_id": session_id,
        "phone": phone,
        "session_id": session_id,
        "summary": summary,
        "transcript": transcript,
        "details": details or {},
        "timestamp": datetime.now().isoformat(),
    }
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            session = await _get_albato_session()
            async with session.post(ALBATO_WEBHOOK_URL, json=payload) as response:
                if response.status in (200, 201, 202):
                    log_info(f"[{session_id}] Лид успешно отправлен в Albato для {phone}")
                    return
                if response.status == 429:
                    log_error(f"[{session_id}] Albato: лимит запросов, лид сохраняем локально для {phone}")
                    await _save_failed_lead_locally(payload)
                    return
                err_body = await response.text()
                log_error(f"[{session_id}] Ошибка Albato [{response.status}] (попытка {attempt}/{max_attempts}): {err_body}")
        except Exception as e:
            log_error(f"[{session_id}] Исключение Albato (попытка {attempt}/{max_attempts}): {e}")
        if attempt < max_attempts:
            await asyncio.sleep(1.5 * attempt)

    log_error(f"[{session_id}] Не удалось отправить лид в Albato для {phone} после {max_attempts} попыток.")
    await _save_failed_lead_locally(payload)

async def _read_lines(path):
    if not os.path.exists(path):
        return []
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return await f.readlines()

async def _rewrite_file(path, lines):
    if lines:
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write("\n".join(lines) + "\n")
            await f.flush()
    else:
        try:
            os.remove(path)
        except Exception:
            pass

async def resend_failed_leads():
    try:
        lines = await _read_lines(FAILED_LEADS_FILE)
    except Exception as e:
        log_error(f"Не удалось прочитать {FAILED_LEADS_FILE}: {e}")
        return
    if not lines:
        return

    log_info(f"Найдено {len(lines)} неотправленных лидов с прошлого запуска, пробуем отправить...")
    still_failed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            log_error(f"В {FAILED_LEADS_FILE} повреждённая JSON-строка, оставляем для ручного разбора")
            still_failed.append(line)
            continue
        sent = False
        try:
            session = await _get_albato_session()
            async with session.post(ALBATO_WEBHOOK_URL, json=payload) as response:
                sent = response.status in (200, 201, 202)
        except Exception:
            sent = False
        if not sent:
            still_failed.append(line)

    await _rewrite_file(FAILED_LEADS_FILE, still_failed)
    sent_count = len(lines) - len(still_failed)
    if sent_count:
        log_info(f"Повторно отправлено {sent_count} лидов, осталось не отправлено: {len(still_failed)}")
