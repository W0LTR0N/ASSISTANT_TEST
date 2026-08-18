import os
from dotenv import load_dotenv
from core.logger import log_info, log_error

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log_error(f"Некорректное значение {name}, использую дефолт {default}")
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log_error(f"Некорректное значение {name}, использую дефолт {default}")
        return default


# ===== Yandex Cloud (STT + GPT) =====
YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY", "")
YANDEX_API_KEY = YANDEX_GPT_API_KEY
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
YANDEX_GPT_MODEL = os.getenv("YANDEX_GPT_MODEL", "yandexgpt-lite/latest")

# ===== GenVoice TTS =====
GENVOICE_API_KEY = os.getenv("GENVOICE_API_KEY", "")
GENVOICE_VOICE_ID = os.getenv("GENVOICE_VOICE_ID", "")
GENVOICE_API_URL = os.getenv("GENVOICE_API_URL", "https://api.genvoice.ru/v1/api/tts")

# ===== SIP (Plusofon) =====
PLUSOFON_SIP_HOST = os.getenv("PLUSOFON_SIP_HOST", "193320.voice.plusofon.ru")
PLUSOFON_SIP_USER = os.getenv("PLUSOFON_SIP_USER", "")
PLUSOFON_SIP_PASSWORD = os.getenv("PLUSOFON_SIP_PASSWORD", "")

# ===== Network =====
PUBLIC_IP = os.getenv("PUBLIC_IP", "").strip()
SIP_CAN_START = bool(PUBLIC_IP) and PUBLIC_IP != "127.0.0.1"

# ===== Albato =====
ALBATO_WEBHOOK_URL = os.getenv("ALBATO_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "default_secret")
ENABLE_CALL_END_WEBHOOK = os.getenv("ENABLE_CALL_END_WEBHOOK", "false").strip().lower() in ("1", "true", "yes", "on")

# ===== Heartbeat =====
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/sip_worker_heartbeat")
HEARTBEAT_INTERVAL = 5
HEARTBEAT_STALE_AFTER = 20

# ===== Storage =====
FAILED_LEADS_FILE = os.getenv("FAILED_LEADS_FILE", "/app/failed_leads.log")

# ===== RTP Ports =====
RTP_PORT_MIN = _env_int("RTP_PORT_MIN", 10000)
RTP_PORT_MAX = _env_int("RTP_PORT_MAX", 10049)

# ===== Call timeouts & VAD =====
IDLE_CALL_TIMEOUT = _env_int("IDLE_CALL_TIMEOUT", 60)
SILENCE_THRESHOLD = _env_int("SILENCE_THRESHOLD", 100)
SILENCE_TO_FINISH = _env_float("SILENCE_TO_FINISH", 0.6)
MAX_UTTERANCE_SEC = _env_float("MAX_UTTERANCE_SEC", 15)
MIN_SPEECH_SEC = _env_float("MIN_SPEECH_SEC", 0.3)

# ===== GPT timeouts =====
LIVE_GPT_TIMEOUT = _env_float("LIVE_GPT_TIMEOUT", 5.0)
SUMMARY_GPT_TIMEOUT = _env_float("SUMMARY_GPT_TIMEOUT", 15.0)

# ===== Bot persona =====
BOT_NAME = os.getenv("BOT_NAME", "Филипп")


def validate_config():
    required = {
        "YANDEX_GPT_API_KEY": YANDEX_GPT_API_KEY,
        "YANDEX_FOLDER_ID": YANDEX_FOLDER_ID,
        "PLUSOFON_SIP_USER": PLUSOFON_SIP_USER,
        "PLUSOFON_SIP_PASSWORD": PLUSOFON_SIP_PASSWORD,
        "ALBATO_WEBHOOK_URL": ALBATO_WEBHOOK_URL,
        "GENVOICE_API_KEY": GENVOICE_API_KEY,
        "GENVOICE_VOICE_ID": GENVOICE_VOICE_ID,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        log_error(
            f"КРИТИЧНО: не заданы переменные окружения: {', '.join(missing)}. "
            f"Проверь .env / настройки окружения в Timeweb/Render."
        )

    if not SIP_CAN_START:
        log_error(
            "КРИТИЧНО: PUBLIC_IP не задан или равен 127.0.0.1. "
            "SIP worker НЕ СТАРТУЕТ: SDP будет содержать локальный адрес, медиа-поток невозможен. "
            "Задай PUBLIC_IP (белый IP сервера) в .env."
        )
    else:
        log_info("Конфигурация проверена, все обязательные переменные на месте.")

    return SIP_CAN_START and len(missing) == 0


validate_config()
