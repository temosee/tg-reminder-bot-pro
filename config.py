import os

TOKEN = os.environ["BOT_TOKEN"]

DATABASE_URL = os.environ["DATABASE_URL"]

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Europe/Moscow")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# Лимиты защиты
MAX_REMINDERS_PER_DAY = int(os.environ.get("MAX_REMINDERS_PER_DAY", "20"))
MAX_ACTIVE_REMINDERS  = int(os.environ.get("MAX_ACTIVE_REMINDERS", "50"))
MAX_MSG_LENGTH        = int(os.environ.get("MAX_MSG_LENGTH", "200"))
FLOOD_LIMIT           = int(os.environ.get("FLOOD_LIMIT", "5"))
FLOOD_WINDOW          = int(os.environ.get("FLOOD_WINDOW", "10"))
MAX_NEW_USERS_PER_HOUR = int(os.environ.get("MAX_NEW_USERS_PER_HOUR", "10"))
