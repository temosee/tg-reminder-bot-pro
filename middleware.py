import time
from collections import defaultdict

import config
import db
from translations import t

_flood_history: dict[int, list] = defaultdict(list)

def check_message(user_id: int, user=None) -> tuple[bool, str | None]:
    if user is None:
        user = db.get_user(user_id)
    if user and user["is_banned"]:
        return False, "banned"

    now = time.time()
    history = _flood_history[user_id]
    _flood_history[user_id] = [t for t in history if now - t < config.FLOOD_WINDOW]
    if len(_flood_history[user_id]) >= config.FLOOD_LIMIT:
        return False, "flood"
    _flood_history[user_id].append(now)

    return True, None

def check_new_reminder(user_id: int) -> tuple[bool, str | None]:
    active = db.get_active_reminders_count(user_id)
    if active >= config.MAX_ACTIVE_REMINDERS:
        return False, t('limit_active', n=config.MAX_ACTIVE_REMINDERS)

    if not db.take_daily_slot(user_id, config.MAX_REMINDERS_PER_DAY):
        return False, t('limit_daily', n=config.MAX_REMINDERS_PER_DAY)

    return True, None

def check_message_length(text: str) -> tuple[bool, str | None]:
    if len(text) > config.MAX_MSG_LENGTH:
        return False, t('msg_too_long', n=config.MAX_MSG_LENGTH)
    return True, None