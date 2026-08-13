import aiohttp
from datetime import datetime
import zoneinfo
from config import ALBATO_WEBHOOK_URL
from core.logger import log_info, log_error

async def send_lead_to_albato(phone: str, summary: str, transcript: str, session_id: str = "", details: dict = None):
    if not ALBATO_WEBHOOK_URL:
        log_error("ALBATO_WEBHOOK_URL не задан в конфигурации")
        return

    try:
        msk_tz = zoneinfo.ZoneInfo("Europe/Moscow")
        current_time = datetime.now(msk_tz).strftime("%Y-%m-%d %H:%M:%S")

        if details is None:
            details = {}

        payload = {
            "phone": phone,
            "summary": summary,
            "client_name": details.get("client_name", "Не указано"),
            "car_model": details.get("car_model", "Не указано"),
            "service": details.get("service", "Не указано"),
            "preferred_time": details.get("preferred_time", "Не указано"),
            "transcript": transcript,
            "session_id": session_id,
            "created_at": current_time,
            "source": "Woltron Voice Bot"
        }

        timeout = aiohttp.ClientTimeout(total=3.0)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(ALBATO_WEBHOOK_URL, json=payload) as response:
                if response.status in (200, 201, 202):
                    log_info(f"Лид успешно отправлен в Albato для {phone}")
                else:
                    err_body = await response.text()
                    log_error(f"Ошибка Albato [{response.status}]: {err_body}")
    except Exception as e:
        log_error(f"Исключение Albato: {e}")
