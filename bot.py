from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import config
from core.logger import log_info, log_error
from core.gpt_logic import clear_session_context
from core.albato_sender import send_lead_to_albato

app = FastAPI(title="Woltron Voice Bot API")

@app.get("/health")
async def healthcheck():
    return {"status": "ok", "service": "woltron-voice-bot"}

@app.post("/webhook/call-end")
async def handle_call_end(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        phone = data.get("phone", "Неизвестный")
        session_id = data.get("session_id", "default")
        summary = data.get("summary", "Разговор завершен")
        transcript = data.get("transcript", "")

        background_tasks.add_task(send_lead_to_albato, phone, summary, transcript, session_id)
        clear_session_context(session_id)

        return JSONResponse({"status": "processed"})
    except Exception as e:
        log_error(f"Ошибка в handle_call_end: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)