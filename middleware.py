import time
from collections import defaultdict

import config
import db

# flood history
_flood_history: dict[int, list] = defaultdict(list)

# daily counter
_daily_counter: dict[int, dict] = {}

def _today_key() -> str:
    import datetime
    return datetime.date.today().isoformat()

def check_message(user_id: int) -> tuple[bool, str | None]:
    user = db.get_user(user_id)
    if user and user["is_banned"]:
        return False, "banned"

    # Антифлуд
    now = time.time()
    history = _flood_history[user_id]
    _flood_history[user_id] = [t for t in history if now - t < config.FLOOD_WINDOW]
    if len(_flood_history[user_id]) >= config.FLOOD_LIMIT:
        return False, "flood"
    _flood_history[user_id].append(now)

    return True, None

def check_new_reminder(user_id: int) -> tuple[bool, str | None]:
    # active limit
    active = db.get_active_reminders_count(user_id)
    if active >= config.MAX_ACTIVE_REMINDERS:
        return False, f"Превышен лимит активных напоминаний ({config.MAX_ACTIVE_REMINDERS})."

    # daily limit
    today = _today_key()
    record = _daily_counter.get(user_id, {})
    if record.get("date") != today:
        record = {"date": today, "count": 0}
    if record["count"] >= config.MAX_REMINDERS_PER_DAY:
        return False, f"Лимит напоминаний на сегодня исчерпан ({config.MAX_REMINDERS_PER_DAY}/день)."
    record["count"] += 1
    _daily_counter[user_id] = record

    return True, None

def check_message_length(text: str) -> tuple[bool, str | None]:
    if len(text) > config.MAX_MSG_LENGTH:
        return False, f"Слишком длинное сообщение (макс. {config.MAX_MSG_LENGTH} символов)."
    return True, None