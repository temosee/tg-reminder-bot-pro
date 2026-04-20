"""
Точка входа для PythonAnywhere (webhook режим).
- Flask обрабатывает запросы синхронно (проверено — работает)
- BackgroundScheduler отправляет напоминания в фоновом потоке
- scheduler.py monkey-patched для совместимости с bot.py
"""
import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

import requests

from flask import Flask, request
import telegram
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import db
import scheduler as sched
from bot import cmd_start, cmd_help, handle_callback, handle_message, handle_voice

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
_lock = threading.Lock()
_bot_app = None
_main_loop = None

# Синхронные отправщики для BackgroundScheduler

def _tg_send(payload: dict, retries: int = 3):
    """Прямой вызов Telegram API через requests — работает в любом потоке."""
    url = f"https://api.telegram.org/bot{config.TOKEN}/sendMessage"
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            logger.info(f"tg_send → {resp.status_code}")
            return
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                logger.error(f"tg_send error after {retries} attempts: {e}")

def _sync_send_once(chat_id: int, message: str, reminder_id: int):
    logger.info(f"_sync_send_once вызван: reminder_id={reminder_id}")
    sched.PENDING_SNOOZE[reminder_id] = {"message": message, "chat_id": chat_id}
    _tg_send({
        "chat_id": chat_id,
        "text": f"⏰ {message}",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "⏩ +15 мин", "callback_data": f"snooze_{reminder_id}_15"},
                    {"text": "⏩ +1 час",  "callback_data": f"snooze_{reminder_id}_60"},
                ],
                [
                    {"text": "⏩ +3 часа", "callback_data": f"snooze_{reminder_id}_180"},
                    {"text": "⏩ Завтра",  "callback_data": f"snooze_{reminder_id}_1440"},
                ],
                [{"text": "✅ Принял", "callback_data": f"dismiss_{reminder_id}"}],
            ]
        },
    })
    db.delete_reminder_by_id(reminder_id)

def _sync_send_recurring(chat_id: int, message: str, reminder_id: int, interval_seconds: int):
    logger.info(f"_sync_send_recurring вызван: reminder_id={reminder_id}")
    _tg_send({"chat_id": chat_id, "text": f"🔔 {message}"})
    db.update_next_fire(reminder_id, time.time() + interval_seconds)

# Синхронные обёртки для планировщика (заменяют sched.add_once_job и т.д.)

_bg_scheduler = BackgroundScheduler()
# НЕ запускаем здесь — запускаем внутри _get_bot_app() чтобы избежать
# проблемы с pre-fork (thread не выживает после fork)

def _add_once_job(bot, reminder_id: int, chat_id: int, message: str, next_fire: float):
    run_date = datetime.fromtimestamp(next_fire, tz=timezone.utc)
    _bg_scheduler.add_job(
        _sync_send_once,
        trigger=DateTrigger(run_date=run_date),
        args=[chat_id, message, reminder_id],
        id=f"once_{reminder_id}",
        replace_existing=True,
    )
    logger.info(f"Job once_{reminder_id} добавлен на {run_date}")

def _add_recurring_job(bot, reminder_id: int, chat_id: int, message: str,
                       interval_seconds: int, start_date=None):
    if start_date is None:
        start_date = datetime.now(tz=timezone.utc)
        db.update_next_fire(reminder_id, time.time() + interval_seconds)
    _bg_scheduler.add_job(
        _sync_send_recurring,
        trigger=IntervalTrigger(seconds=interval_seconds, start_date=start_date),
        args=[chat_id, message, reminder_id, interval_seconds],
        id=f"recurring_{reminder_id}",
        replace_existing=True,
    )
    logger.info(f"Job recurring_{reminder_id} добавлен, интервал {interval_seconds}с")

def _remove_job(reminder_id: int, type_: str):
    job_id = f"once_{reminder_id}" if type_ == "once" else f"recurring_{reminder_id}"
    try:
        _bg_scheduler.remove_job(job_id)
    except Exception:
        pass

# Monkey-patch: bot.py будет вызывать эти функции через sched.*
sched.add_once_job = _add_once_job
sched.add_recurring_job = _add_recurring_job
sched.remove_job = _remove_job

# Инициализация бота

def _get_bot_app():
    global _bot_app, _main_loop
    if _bot_app is not None:
        return _bot_app
    with _lock:
        if _bot_app is not None:
            return _bot_app
        db.init_db()
        app = Application.builder().token(config.TOKEN).build()
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.VOICE, handle_voice))

        _main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_main_loop)
        # Retry на случай временной недоступности прокси PythonAnywhere
        for attempt in range(5):
            try:
                _main_loop.run_until_complete(app.initialize())
                break
            except Exception as e:
                if attempt < 4:
                    logger.warning(f"initialize attempt {attempt+1} failed: {e}, retrying...")
                    time.sleep(2)
                else:
                    raise
        _main_loop.run_until_complete(app.start())
        _bot_app = app

        # Запускаем scheduler здесь — после fork, в реальном worker-процессе
        if not _bg_scheduler.running:
            _bg_scheduler.start()
            logger.info(f"Scheduler запущен, pid={__import__('os').getpid()}")
        sched.restore_jobs(None)
        logger.info("Бот инициализирован")
    return _bot_app

# Flask endpoints

@flask_app.route(f"/webhook/{config.TOKEN}", methods=["POST"])
def webhook():
    app = _get_bot_app()
    data = request.get_json(force=True)
    update = telegram.Update.de_json(data, app.bot)
    _main_loop.run_until_complete(app.process_update(update))
    return "ok"

@flask_app.route("/")
def index():
    jobs = len(_bg_scheduler.get_jobs())
    running = _bg_scheduler.running
    return f"Bot is running! Scheduler running: {running}, jobs: {jobs}"

application = flask_app
