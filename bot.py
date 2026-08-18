import time
import os
from fastapi import FastAPI, Request, BackgroundTasks, Header
from fastapi.responses import JSONResponse
import config
from core.logger import log_info, log_error
from core.gpt_logic import clear_session_context
from core.albato_sender import send_lead_to_albato

app = FastAPI(title="Woltron Voice Bot API")


@app.get("/health")
async def healthcheck():
    """Проверяет не только то, что FastAPI жив, но и то, что sip_worker.py
    (отдельный процесс) реально обрабатывает события, через heartbeat-файл."""
    sip_alive = False
    last_seen_seconds_ago = None
    try:
        if os.path.exists(config.HEARTBEAT_FILE):
            mtime = os.path.getmtime(config.HEARTBEAT_FILE)
            last_seen_seconds_ago = int(time.time() - mtime)
            sip_alive = last_seen_seconds_ago <= config.HEARTBEAT_STALE_AFTER
    except Exception as e:
        log_error(f"Ошибка в /health: {e}")
    return {
        "status": "ok",
        "sip_alive": sip_alive,
        "last_seen_seconds_ago": last_seen_seconds_ago,
        "fastapi": True,
    }


@app.post("/webhook/call-end")
async def handle_call_end(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str = Header(default=""),
):
    # Источник правды по лидам — sip_worker.stop_call().
    # Вебхук выключен по умолчанию (ENABLE_CALL_END_WEBHOOK=false): один звонок = один лид.
    if not config.ENABLE_CALL_END_WEBHOOK:
        return JSONResponse({"status": "error", "message": "disabled"}, status_code=410)

    if config.WEBHOOK_SECRET and x_webhook_secret != config.WEBHOOK_SECRET:
        log_error("Попытка обращения к /webhook/call-end с неверным секретом")
        return JSONResponse({"status": "error", "message": "unauthorized"}, status_code=401)
    try:
        data = await request.json()
        phone = data.get("phone", "Неизвестный")
        session_id = data.get("session_id", "default")
        transcript = data.get("transcript", [])
        summary = data.get("summary", "")
        details = data.get("details", {})
        background_tasks.add_task(send_lead_to_albato, phone, summary, transcript, session_id, details)
        # Примечание: история диалогов живёт в процессе sip_worker;
        # очистка здесь работает только если вебхук дёрнут из того же процесса.
        if session_id:
            clear_session_context(session_id)
        return {"status": "received"}
    except Exception as e:
        log_error(f"Ошибка в /webhook/call-end: {e}")
        return JSONResponse({"status": "error", "message": "internal server error"}, status_code=500)
