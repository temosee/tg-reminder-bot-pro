import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

import db
from translations import t


def _user_lang(user_id: int) -> str:
    u = db.get_user(user_id)
    return (u.get('language') or 'ru') if u else 'ru'

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 10, "coalesce": True})

PENDING_SNOOZE = {}

def _job_id_once(reminder_id: int) -> str:
    return f"once_{reminder_id}"

def _job_id_recurring(reminder_id: int) -> str:
    return f"recurring_{reminder_id}"

async def _send_once(bot, chat_id: int, message: str, reminder_id: int):
    lang = _user_lang(chat_id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, 'snooze_15m'), callback_data=f"snooze_{reminder_id}_15"),
            InlineKeyboardButton(t(lang, 'snooze_1h'),  callback_data=f"snooze_{reminder_id}_60"),
        ],
        [
            InlineKeyboardButton(t(lang, 'snooze_3h'), callback_data=f"snooze_{reminder_id}_180"),
            InlineKeyboardButton(t(lang, 'snooze_tomorrow'), callback_data=f"snooze_{reminder_id}_1440"),
        ],
        [InlineKeyboardButton(t(lang, 'btn_done'), callback_data=f"dismiss_{reminder_id}")],
    ])
    PENDING_SNOOZE[reminder_id] = {"message": message, "chat_id": chat_id}
    try:
        await bot.send_message(chat_id=chat_id, text=f"⏰ {message}", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка отправки разового напоминания {reminder_id}: {e}")
    finally:
        db.delete_reminder_by_id(reminder_id)

async def _send_recurring(bot, chat_id: int, message: str, reminder_id: int, interval_seconds: int):
    try:
        await bot.send_message(chat_id=chat_id, text=f"🔔 {message}")
        db.update_next_fire(reminder_id, time.time() + interval_seconds)
    except Exception as e:
        logger.error(f"Ошибка отправки повторяющегося напоминания: {e}")

def add_once_job(bot, reminder_id: int, chat_id: int, message: str, next_fire: float):
    run_date = datetime.fromtimestamp(next_fire, tz=timezone.utc)
    scheduler.add_job(
        _send_once,
        trigger=DateTrigger(run_date=run_date),
        args=[bot, chat_id, message, reminder_id],
        id=_job_id_once(reminder_id),
        replace_existing=True,
    )

def add_recurring_job(bot, reminder_id: int, chat_id: int, message: str,
                      interval_seconds: int, start_date: datetime = None):
    if start_date is None:
        start_date = datetime.now(tz=timezone.utc)
        db.update_next_fire(reminder_id, time.time() + interval_seconds)

    scheduler.add_job(
        _send_recurring,
        trigger=IntervalTrigger(seconds=interval_seconds, start_date=start_date),
        args=[bot, chat_id, message, reminder_id, interval_seconds],
        id=_job_id_recurring(reminder_id),
        replace_existing=True,
    )

def remove_job(reminder_id: int, type_: str):
    job_id = _job_id_once(reminder_id) if type_ == "once" else _job_id_recurring(reminder_id)
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

def restore_jobs(bot):
    rows = db.get_all_reminders()
    for row in rows:
        if row["type"] == "once":
            if row["next_fire"] and row["next_fire"] > time.time():
                add_once_job(bot, row["id"], row["chat_id"], row["message"], row["next_fire"])
            else:
                lang = _user_lang(row["chat_id"])
                overdue_message = t(lang, 'overdue', msg=row['message'])
                add_once_job(bot, row["id"], row["chat_id"], overdue_message, time.time() + 5)

        elif row["type"] == "recurring":
            next_fire = row["next_fire"]
            if next_fire:
                now = time.time()
                if next_fire < now:
                    elapsed = now - next_fire
                    periods_missed = int(elapsed / row["interval_seconds"]) + 1
                    next_fire = next_fire + periods_missed * row["interval_seconds"]
                    db.update_next_fire(row["id"], next_fire)
                start_date = datetime.fromtimestamp(next_fire, tz=timezone.utc)
            else:
                start_date = None

            add_recurring_job(bot, row["id"], row["chat_id"], row["message"],
                               row["interval_seconds"], start_date=start_date)
