import asyncio
import logging
import time
import zoneinfo
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

import db
from translations import t


def _user_lang(user_id: int) -> str:
    u = db.get_user(user_id)
    return (u.get('language') or 'ru') if u else 'ru'

def _user_tz(user_id: int) -> str | None:
    u = db.get_user(user_id)
    return u.get('timezone') if u else None

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 600, "coalesce": True})

PENDING_SNOOZE = {}

def _job_id_once(reminder_id: int) -> str:
    return f"once_{reminder_id}"

def _job_id_recurring(reminder_id: int) -> str:
    return f"recurring_{reminder_id}"

async def _send_once(bot, chat_id: int, message: str, reminder_id: int):
    lang = await asyncio.to_thread(_user_lang, chat_id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, 'snooze_15m'), callback_data=f"snooze_{reminder_id}_15"),
            InlineKeyboardButton(t(lang, 'snooze_30m'), callback_data=f"snooze_{reminder_id}_30"),
        ],
        [
            InlineKeyboardButton(t(lang, 'snooze_1h'), callback_data=f"snooze_{reminder_id}_60"),
            InlineKeyboardButton(t(lang, 'snooze_3h'), callback_data=f"snooze_{reminder_id}_180"),
        ],
        [
            InlineKeyboardButton(t(lang, 'snooze_6h'), callback_data=f"snooze_{reminder_id}_360"),
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
        await _delete_sent(reminder_id)

async def _delete_sent(reminder_id: int):
    # если запись не удалить, при следующем старте она уйдёт повторно
    for attempt in range(3):
        try:
            await asyncio.to_thread(db.delete_reminder_by_id, reminder_id)
            return
        except Exception as e:
            logger.error(f"Не удалось удалить напоминание {reminder_id}: {e}")
            await asyncio.sleep(2 * (attempt + 1))

async def _send_recurring(bot, chat_id: int, message: str, reminder_id: int, interval_seconds: int):
    try:
        await bot.send_message(chat_id=chat_id, text=f"🔔 {message}")
    except Exception as e:
        logger.error(f"Ошибка отправки повторяющегося напоминания: {e}")
    try:
        await asyncio.to_thread(db.update_next_fire, reminder_id, time.time() + interval_seconds)
    except Exception as e:
        logger.error(f"Не удалось обновить next_fire у {reminder_id}: {e}")

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
                      interval_seconds: int, start_date: datetime = None,
                      until: float = None):
    if start_date is None:
        start_date = datetime.now(tz=timezone.utc)
        db.update_next_fire(reminder_id, time.time() + interval_seconds)

    scheduler.add_job(
        _send_recurring,
        trigger=IntervalTrigger(seconds=interval_seconds, start_date=start_date,
                                end_date=_end_date(until)),
        args=[bot, chat_id, message, reminder_id, interval_seconds],
        id=_job_id_recurring(reminder_id),
        replace_existing=True,
    )

def _end_date(until: float):
    return datetime.fromtimestamp(until, tz=timezone.utc) if until else None

CRON_DAYS = {"mon-fri": "mon-fri", "sat,sun": "sat,sun"}

async def _send_cron(bot, chat_id: int, message: str, reminder_id: int):
    try:
        await bot.send_message(chat_id=chat_id, text=f"🔔 {message}")
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания по дням недели: {e}")

def add_cron_job(bot, reminder_id: int, chat_id: int, message: str,
                 days: str, at_time: str, tz_name: str = None, until: float = None):
    hour, minute = (at_time or "09:00").split(":")
    try:
        tz = zoneinfo.ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    scheduler.add_job(
        _send_cron,
        trigger=CronTrigger(day_of_week=CRON_DAYS.get(days, "mon-fri"),
                            hour=int(hour), minute=int(minute),
                            timezone=tz, end_date=_end_date(until)),
        args=[bot, chat_id, message, reminder_id],
        id=_job_id_recurring(reminder_id),
        replace_existing=True,
    )

async def _maintenance():
    try:
        await asyncio.to_thread(db.purge_old_usage)
        await asyncio.to_thread(db.delete_expired_reminders)
    except Exception as e:
        logger.error(f"Ошибка ночной уборки: {e}")

def add_maintenance_job():
    scheduler.add_job(
        _maintenance,
        trigger=IntervalTrigger(hours=24, start_date=datetime.now(tz=timezone.utc)),
        id="maintenance",
        replace_existing=True,
    )

def remove_job(reminder_id: int, type_: str):
    job_id = _job_id_once(reminder_id) if type_ == "once" else _job_id_recurring(reminder_id)
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

def restore_jobs(bot):
    try:
        rows = db.get_all_reminders()
    except Exception as e:
        logger.error(f"Не удалось прочитать напоминания при старте: {e}")
        return
    overdue_offset = 0
    for row in rows:
        try:
            if row.get("until") and row["until"] < time.time():
                db.delete_reminder_by_id(row["id"])
                continue

            if row.get("days_of_week"):
                add_cron_job(bot, row["id"], row["chat_id"], row["message"],
                             row["days_of_week"], row.get("at_time"),
                             _user_tz(row["chat_id"]), row.get("until"))
                continue

            if row["type"] == "once":
                if row["next_fire"] and row["next_fire"] > time.time():
                    add_once_job(bot, row["id"], row["chat_id"], row["message"], row["next_fire"])
                else:
                    lang = _user_lang(row["chat_id"])
                    overdue_message = t(lang, 'overdue', msg=row['message'])
                    overdue_offset += 2
                    add_once_job(bot, row["id"], row["chat_id"], overdue_message, time.time() + 5 + overdue_offset)

            elif row["type"] == "recurring":
                if not row.get("interval_seconds"):
                    logger.warning(f"Skip recurring {row['id']}: no interval_seconds")
                    continue
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
                                   row["interval_seconds"], start_date=start_date,
                                   until=row.get("until"))
        except Exception as e:
            logger.error(f"Failed to restore reminder {row.get('id')}: {e}")
