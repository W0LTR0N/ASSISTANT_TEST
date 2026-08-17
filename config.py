import os
from dotenv import load_dotenv

load_dotenv()

YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY", "")
YANDEX_API_KEY = YANDEX_GPT_API_KEY

YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
YANDEX_GPT_MODEL = os.getenv("YANDEX_GPT_MODEL", "yandexgpt-lite/latest")

PLUSOFON_SIP_USER = os.getenv("PLUSOFON_SIP_USER", "193320")
PLUSOFON_SIP_PASSWORD = os.getenv("PLUSOFON_SIP_PASSWORD", "")
PLUSOFON_SIP_HOST = os.getenv("PLUSOFON_SIP_HOST", "193320.voice.plusofon.ru")

ALBATO_WEBHOOK_URL = os.getenv("ALBATO_WEBHOOK_URL", "")
PUBLIC_IP = os.getenv("PUBLIC_IP", "127.0.0.1")
