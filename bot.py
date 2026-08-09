import asyncio
import concurrent.futures
import functools
import html
import logging
import re
import time as _time
from datetime import datetime, timezone
import zoneinfo

import psycopg2
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import NetworkError
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

import config
import db
import middleware
import parser as reminder_parser
import scheduler as sched
import admin as admin_handlers
import city_tz
from groq import Groq
from translations import t

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# потоков ровно столько же, сколько соединений в пуле, иначе часть упрётся в пустой пул
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=config.DB_POOL_SIZE)

async def _run(func, *args, **kwargs):
    """run blocking call in threadpool"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, functools.partial(func, *args, **kwargs))

def get_keyboard():
    return ReplyKeyboardMarkup(
        [
            [t('btn_reminders'), t('btn_stats')],
            [t('btn_notes')],
            [t('btn_settings')],
        ],
        resize_keyboard=True,
        input_field_placeholder=t('placeholder'),
    )

def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many

_UNITS_RU = {
    'sec':  ("каждую секунду", ("секунду", "секунды", "секунд")),
    'min':  ("каждую минуту",  ("минуту", "минуты", "минут")),
    'hour': ("каждый час",     ("час", "часа", "часов")),
    'day':  ("каждый день",    ("день", "дня", "дней")),
    'week': ("каждую неделю",  ("неделю", "недели", "недель")),
}
def _every(n: int, unit: str) -> str:
    single, forms = _UNITS_RU[unit]
    if n == 1:
        return single
    return f"каждые {n} {_plural_ru(n, *forms)}"

def format_interval(seconds: int) -> str:
    if seconds % 604800 == 0:
        return _every(seconds // 604800, 'week')
    if seconds % 86400 == 0:
        return _every(seconds // 86400, 'day')
    if seconds % 3600 == 0:
        return _every(seconds // 3600, 'hour')
    if seconds % 60 == 0:
        return _every(seconds // 60, 'min')
    return _every(seconds, 'sec')

INTENT_NOTES_LIST = [
    "мои заметки", "покажи заметки", "все заметки", "заметки",
    "🗒 заметки", "список заметок", "покажи все заметки",
]

INTENT_NOTES_ADD = re.compile(
    r"^\s*запомни(?:\s+что)?\s*:?\s+(.+)$",
    re.IGNORECASE | re.DOTALL
)

INTENT_NOTES_DELETE = re.compile(
    r"^\s*забудь\s+про\s+(.+)$",
    re.IGNORECASE | re.DOTALL
)

_AMPM_REPLY = re.compile(r'\b(am|pm)\b', re.IGNORECASE)

INTENT_LIST = [
    "мои напоминания", "список напоминаний", "покажи напоминания",
    "какие напоминания", "что стоит", "что у меня", "список",
    "все напоминания", "покажи все", "напоминалки",
    "📋 мои напоминания",
    "напоминания", "напоминание", "что напомнить", "что поставлено",
    "какие стоят", "что активно", "активные напоминания",
    "покажи список", "покажи что стоит", "что у меня стоит",
    "мои дела", "что запланировано", "какие есть напоминания",
    "есть напоминания", "есть что", "что там", "покажи",
    "текущие напоминания", "сколько напоминаний",
]

INTENT_DELETE_ALL = [
    "удали все", "удалить все", "убери все", "удали всё",
    "удалить всё", "очисти все", "очисти всё", "удали все напоминания",
    "убери все напоминания", "сотри все", "сотри всё",
    "сотри все напоминания", "удалить все напоминания",
    "очисти список", "снеси все", "снеси всё",
]

ORDINAL_TO_INDEX = {
    "первое": 0, "первый": 0, "первую": 0,
    "второе": 1, "второй": 1, "вторую": 1,
    "третье": 2, "третий": 2, "третью": 2,
    "четвёртое": 3, "четвертое": 3, "четвёртый": 3, "четвертый": 3,
    "пятое": 4, "пятый": 4,
    "шестое": 5, "шестой": 5,
    "седьмое": 6, "седьмой": 6,
    "восьмое": 7, "восьмой": 7,
    "девятое": 8, "девятый": 8,
    "десятое": 9, "десятый": 9,
    "последнее": -1, "последний": -1, "последнюю": -1,
    "крайнее": -1, "крайний": -1,
    "предпоследнее": -2, "предпоследний": -2,
}

INTENT_DELETE_ORDINAL = re.compile(
    r"(удали|удалить|убери|убрать|отмени|отменить|сотри|стереть)\s+"
    r"(напоминание\s+)?"
    r"(первое|первый|первую|второе|второй|вторую|третье|третий|третью"
    r"|четвёртое|четвертое|четвёртый|четвертый|пятое|пятый"
    r"|шестое|шестой|седьмое|седьмой|восьмое|восьмой"
    r"|девятое|девятый|десятое|десятый"
    r"|последнее|последний|последнюю|крайнее|крайний"
    r"|предпоследнее|предпоследний)"
    r"(\s+напоминание)?",
    re.IGNORECASE
)


INTENT_DELETE_N = re.compile(
    r"(удали|удалить|убери|убрать|отмени|отменить)\s+"
    r"(напоминание\s+)?(?:#?(\d+)|номер\s+(\d+)|под\s+номером\s+(\d+))"
)


def _too_many_newcomers() -> bool:
    try:
        return db.count_new_users_since(_time.time() - 3600) >= config.MAX_NEW_USERS_PER_HOUR
    except Exception as e:
        logger.error(f"Не удалось посчитать новых пользователей: {e}")
        return False

def _looks_like_reminder(text: str) -> bool:
    if not text:
        return False
    try:
        parsed = reminder_parser.parse(text)
    except Exception:
        return False
    if parsed.get("error"):
        return False
    return bool(parsed.get("next_fire") or parsed.get("interval_seconds"))

def _tz(tz_name: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(tz_name or "UTC")
    except Exception:
        return zoneinfo.ZoneInfo("UTC")

def _get_user_tz(user_id: int) -> zoneinfo.ZoneInfo:
    user = db.get_user(user_id)
    return _tz(user["timezone"] if user else "UTC")

def _local_dt(ts: float, user_id: int, tz_name: str = None) -> datetime:
    tz = _tz(tz_name) if tz_name else _get_user_tz(user_id)
    return datetime.fromtimestamp(ts, tz=tz)

def _fmt_time(dt: datetime) -> str:
    return dt.strftime('%H:%M')

def _fmt_datetime(dt: datetime) -> str:
    year = '.%y' if dt.year != datetime.now(tz=dt.tzinfo).year else ''
    return dt.strftime(f'%H:%M %d.%m{year}')

PAGE_SIZE = 8
NOTES_PAGE_SIZE = 5   # заметка до 500 символов, больше пяти в одно сообщение не влезет
TG_TEXT_LIMIT = 4096

def tg_len(text: str) -> int:
    """Телеграм считает длину в единицах UTF-16: эмодзи занимает две, а не одну."""
    return len(text.encode("utf-16-le")) // 2

def _fit(lines: list[str]) -> str:
    """Собирает сообщение так, чтобы телеграм его точно принял."""
    text = chr(10).join(lines)
    budget = TG_TEXT_LIMIT - 96
    if tg_len(text) <= budget:
        return text
    trimmed = []
    for line in lines:
        if tg_len(line) > budget:
            cut = line
            while cut and tg_len(cut) > max(0, budget - 1):
                cut = cut[:-1]
            trimmed.append(cut + "…")
            break
        trimmed.append(line)
        budget -= tg_len(line) + 1
    return chr(10).join(trimmed)

def _page_nav(page: int, total: int, prefix: str, size: int = PAGE_SIZE):
    pages = max(1, (total + size - 1) // size)
    if pages == 1:
        return None, pages
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("‹", callback_data=f"{prefix}{page - 1}"))
    row.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("›", callback_data=f"{prefix}{page + 1}"))
    return row, pages

def build_reminders_message(reminders, user_id: int, tz_name: str = None, page: int = 0):
    if tz_name is None:
        tz_name = str(_get_user_tz(user_id))
    total = len(reminders)
    nav, pages = _page_nav(page, total, "rpage_")
    page = max(0, min(page, pages - 1))
    nav, pages = _page_nav(page, total, "rpage_")
    chunk = reminders[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [t('reminders_header')]
    buttons = []
    for offset, r in enumerate(chunk):
        n = page * PAGE_SIZE + offset + 1
        if r.get("days_of_week"):
            days_str = t('days_weekdays' if r["days_of_week"] == "mon-fri" else 'days_weekend')
            label = t('label_days', days=days_str, time=r.get("at_time") or "09:00", msg=r['message'])
        elif r["type"] == "recurring":
            interval_str = format_interval(r["interval_seconds"])
            if r.get("next_fire") and r["interval_seconds"] >= 86400:
                dt = _local_dt(r["next_fire"], user_id, tz_name)
                label = t('label_recurring_at', interval=interval_str, time=_fmt_time(dt), msg=r['message'])
            else:
                label = t('label_recurring', interval=interval_str, msg=r['message'])
        else:
            dt = _local_dt(r["next_fire"], user_id, tz_name)
            label = t('label_once', time=_fmt_datetime(dt), msg=r['message'])
        lines.append(f"{n}. {html.escape(label)}")
        row = []
        # у напоминаний по дням недели переносить нечего — только удалить
        if not r.get("days_of_week"):
            row.append(InlineKeyboardButton(t('btn_move_n', n=n), callback_data=f"move_{r['id']}"))
        row.append(InlineKeyboardButton(t('btn_delete_n', n=n), callback_data=f"del_{r['id']}"))
        buttons.append(row)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(t('btn_close'), callback_data="close")])
    if pages > 1:
        lines.append("")
        lines.append(t('page_of', page=page + 1, pages=pages, total=total))
    return _fit(lines), InlineKeyboardMarkup(buttons)

MOVE_OPTIONS = [
    ('snooze_15m', 15), ('snooze_30m', 30),
    ('snooze_1h', 60), ('snooze_3h', 180),
    ('snooze_6h', 360), ('move_1d', 1440),
]

def build_move_menu(reminder_id: int):
    rows = []
    for i in range(0, len(MOVE_OPTIONS), 2):
        rows.append([
            InlineKeyboardButton(t(key), callback_data=f"mv_{reminder_id}_{mins}")
            for key, mins in MOVE_OPTIONS[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(t('btn_back'), callback_data="back_list")])
    return InlineKeyboardMarkup(rows)

_HANDLED_TAPS: dict[tuple, float] = {}

def already_handled(chat_id, message_id, data) -> bool:
    """Второе нажатие той же кнопки (лаг сети, дрожь пальца) не должно срабатывать дважды."""
    now = _time.time()
    for key, ts in list(_HANDLED_TAPS.items()):
        if now - ts > 300:
            _HANDLED_TAPS.pop(key, None)
    key = (chat_id, message_id, data)
    if key in _HANDLED_TAPS:
        return True
    _HANDLED_TAPS[key] = now
    return False

def recover_snooze_info(message):
    # после рестарта список в памяти пуст, но текст напоминания есть в самом сообщении
    shown = (getattr(message, "text", None) or "").strip()
    if shown.startswith("⏰"):
        shown = shown[1:].strip()
    if not shown:
        return None
    return {"message": shown, "chat_id": message.chat_id}

def build_notes_message(notes, page: int = 0):
    total = len(notes)
    nav, pages = _page_nav(page, total, "npage_", NOTES_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    nav, pages = _page_nav(page, total, "npage_", NOTES_PAGE_SIZE)
    chunk = notes[page * NOTES_PAGE_SIZE:(page + 1) * NOTES_PAGE_SIZE]

    lines = [t('notes_header')]
    buttons = []
    for offset, n in enumerate(chunk):
        num = page * NOTES_PAGE_SIZE + offset + 1
        lines.append(f"{num}. {html.escape(n['text'])}")
        buttons.append([InlineKeyboardButton(t('btn_delete_n', n=num),
                                             callback_data=f"delnote_{n['id']}")])
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(t('btn_close'), callback_data="close")])
    if pages > 1:
        lines.append("")
        lines.append(t('page_of', page=page + 1, pages=pages, total=total))
    return _fit(lines), InlineKeyboardMarkup(buttons)

async def show_notes(update: Update, user_id: int):
    notes = db.get_notes(user_id)
    kb = get_keyboard()
    if not notes:
        await update.message.reply_text(t('no_notes'), reply_markup=kb)
        return
    text, keyboard = build_notes_message(notes)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def _blocked(update: Update) -> bool:
    row = await _run(db.get_user, update.effective_user.id)
    return bool(row and row["is_banned"])

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await _blocked(update):
        return
    if not db.get_user(user.id) and _too_many_newcomers():
        logger.warning("Достигнут предел новых пользователей в час")
        return
    is_new = db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
    tz_set = user_row and user_row["timezone"] != "UTC"

    if is_new or not tz_set:
        context.user_data["awaiting_city"] = True
        await update.message.reply_text(t('welcome_new', name=user.first_name))
        return

    await update.message.reply_text(
        t('welcome_back', name=user.first_name),
        reply_markup=get_keyboard(),
    )

async def _send_welcome(update: Update):
    await update.message.reply_text(
        t('welcome_features'),
        reply_markup=get_keyboard(),
    )

async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await _blocked(update):
        return
    db.register_user(user.id, user.username or "", user.first_name or "")
    context.user_data["awaiting_city"] = True
    await update.message.reply_text(t('awaiting_city'))

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await _blocked(update):
        return
    db.register_user(user.id, user.username or "", user.first_name or "")
    stats = db.get_user_stats(user.id)
    await update.message.reply_text(
        t('stats', total=stats['total_created'], active=stats['active']),
        parse_mode="HTML", reply_markup=get_keyboard()
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def show_reminders(update: Update, user_id: int, tz_name: str = None):
    reminders = await _run(db.get_reminders, user_id)
    if not reminders:
        await update.message.reply_text(t('no_reminders'), reply_markup=get_keyboard())
        return
    text, keyboard = build_reminders_message(reminders, user_id, tz_name)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await _dispatch_callback(update, context)
    except ValueError:
        logger.warning(f"Непонятные данные кнопки: {query.data!r}")

async def _dispatch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    user_row = await _run(db.get_user, query.from_user.id)

    if user_row and user_row["is_banned"]:
        return

    if query.data.startswith(("rpage_", "npage_")):
        page = int(query.data.split("_")[1])
        if query.data.startswith("rpage_"):
            rows = await _run(db.get_reminders, query.from_user.id)
            if not rows:
                await query.edit_message_text(t('no_reminders'))
                return
            text, keyboard = build_reminders_message(rows, query.from_user.id, user_row["timezone"] if user_row else None, page)
        else:
            rows = await _run(db.get_notes, query.from_user.id)
            if not rows:
                await query.edit_message_text(t('no_notes'))
                return
            text, keyboard = build_notes_message(rows, page)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return


    # timezone change
    if query.data == "settings_tz":
        await query.edit_message_text(t('settings_tz_prompt'))
        context.user_data["awaiting_city"] = True
        return

    # noop
    if query.data == "noop":
        return

    # close
    if query.data == "close":
        await query.delete_message()
        return

    # confirm delete all
    if query.data == "delall_yes":
        reminders = await _run(db.get_reminders, query.from_user.id)
        for r in reminders:
            sched.remove_job(r["id"], r["type"])
            await _run(db.delete_reminder, r["id"], query.from_user.id)
        await query.edit_message_text(t('all_deleted', count=len(reminders)))
        return
    if query.data == "delall_no":
        await query.edit_message_text(t('delete_cancelled'))
        return

    tz_name = user_row["timezone"] if user_row else None

    # move existing reminder
    if query.data.startswith("move_"):
        rid = int(query.data[5:])
        target = await _run(db.get_reminder, rid, query.from_user.id)
        if not target:
            await query.edit_message_text(t('already_deleted'))
            return
        prompt = 'shift_prompt' if target["type"] == "recurring" else 'move_prompt'
        await query.edit_message_text(
            t(prompt, msg=target["message"]),
            reply_markup=build_move_menu(rid),
        )
        return

    if query.data.startswith("mv_"):
        _, rid_str, mins_str = query.data.split("_")
        rid, minutes = int(rid_str), int(mins_str)
        target = await _run(db.get_reminder, rid, query.from_user.id)
        if not target:
            await query.edit_message_text(t('already_deleted'))
            return
        if target.get("days_of_week"):
            await query.edit_message_text(t('move_unsupported'))
            return
        recurring = target["type"] == "recurring" and target.get("interval_seconds")
        if recurring:
            # сдвигаем расписание от запланированного времени, а не от «сейчас»,
            # иначе повторяющееся молча меняет час срабатывания
            new_fire = (target.get("next_fire") or _time.time()) + minutes * 60
            while new_fire <= _time.time():
                new_fire += target["interval_seconds"]
        else:
            new_fire = _time.time() + minutes * 60

        await _run(db.update_next_fire, rid, new_fire)
        sched.remove_job(rid, target["type"])
        if recurring:
            sched.add_recurring_job(
                context.bot, rid, target["chat_id"], target["message"],
                target["interval_seconds"],
                start_date=datetime.fromtimestamp(new_fire, tz=timezone.utc),
                until=target.get("until"),
            )
        else:
            sched.add_once_job(context.bot, rid, target["chat_id"], target["message"], new_fire)
        dt = _local_dt(new_fire, query.from_user.id, tz_name)
        await query.edit_message_text(
            t('shifted' if recurring else 'moved',
              msg=target['message'], time=_fmt_datetime(dt))
        )
        return

    # удаление из карточки только что созданного — список тут ни к чему
    if query.data.startswith("delx_"):
        rid = int(query.data[5:])
        target = await _run(db.get_reminder, rid, query.from_user.id)
        if not target:
            await query.edit_message_text(t('already_deleted'))
            return
        sched.remove_job(rid, target["type"])
        await _run(db.delete_reminder, rid, query.from_user.id)
        await query.edit_message_text(t('deleted', msg=target['message']))
        return

    if query.data == "back_list":
        reminders = await _run(db.get_reminders, query.from_user.id)
        if not reminders:
            await query.edit_message_text(t('no_reminders'))
            return
        text, keyboard = build_reminders_message(reminders, query.from_user.id, tz_name)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    # snooze
    if query.data.startswith("snooze_"):
        _, rid_str, mins_str = query.data.split("_")
        reminder_id = int(rid_str)
        minutes = int(mins_str)
        if already_handled(query.message.chat_id, getattr(query.message, "message_id", 0), query.data):
            return
        info = sched.PENDING_SNOOZE.get(reminder_id) or recover_snooze_info(query.message)
        if info:
            new_fire = _time.time() + minutes * 60
            new_id = db.add_reminder(
                user_id=query.from_user.id,
                chat_id=info["chat_id"],
                message=info["message"],
                type_="once",
                next_fire=new_fire,
            )
            sched.add_once_job(context.bot, new_id, info["chat_id"], info["message"], new_fire)
            sched.PENDING_SNOOZE.pop(reminder_id, None)
            dt = _local_dt(new_fire, query.from_user.id, user_row["timezone"] if user_row else None)
            await query.edit_message_text(t('snoozed', msg=info['message'], time=_fmt_time(dt)))
        else:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if query.data.startswith("dismiss_"):
        reminder_id = int(query.data.split("_")[1])
        sched.PENDING_SNOOZE.pop(reminder_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # delete note
    if query.data.startswith("delnote_"):
        note_id = int(query.data[8:])
        db.delete_note(note_id, query.from_user.id)
        notes = db.get_notes(query.from_user.id)
        if notes:
            text, keyboard = build_notes_message(notes)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.edit_message_text(t('notes_all_deleted'))
        return

    if not query.data.startswith("del_"):
        return

    # delete reminder
    rid = int(query.data[4:])
    user_id = query.from_user.id

    all_r = db.get_reminders(user_id)
    target = next((r for r in all_r if r["id"] == rid), None)

    if target:
        sched.remove_job(rid, target["type"])
        db.delete_reminder(rid, user_id)
        remaining = db.get_reminders(user_id)
        if remaining:
            text, keyboard = build_reminders_message(remaining, user_id, user_row["timezone"] if user_row else None)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.edit_message_text(t('deleted', msg=target['message']))
    else:
        await query.edit_message_text(t('already_deleted'))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, _text: str = None):
    user = update.effective_user
    user_row = await _run(db.get_user, user.id)
    if user_row is None:
        if await _run(_too_many_newcomers):
            logger.warning("Достигнут предел новых пользователей в час")
            return
        await _run(db.register_user, user.id, user.username or "", user.first_name or "")
        user_row = await _run(db.get_user, user.id)
    kb = get_keyboard()

    if user_row and user_row["is_banned"]:
        return

    # waiting for city
    if context.user_data.get("awaiting_city"):
        city_input = (_text if _text is not None else update.message.text or "").strip()
        if _looks_like_reminder(city_input):
            # прислали напоминание вместо города — не держим человека в этом шаге
            context.user_data.pop("awaiting_city", None)
        else:
            await update.message.reply_text(t('city_searching'))
            tz, display = await _run(city_tz.city_to_timezone, city_input)
            if tz:
                old_tz = user_row["timezone"] if user_row else "UTC"
                await _run(db.update_timezone, user.id, tz)
                context.user_data.pop("awaiting_city", None)
                user_row = await _run(db.get_user, user.id)
                moved = await _run(sched.reschedule_user, context.bot, user.id, old_tz, tz)
                note = "\n\n" + t('tz_moved', n=moved) if moved else ""
                await update.message.reply_text(t('city_set', display=display) + note, reply_markup=kb)
                if old_tz in (None, "UTC"):
                    await _send_welcome(update)
            else:
                await update.message.reply_text(t('city_not_found', city=city_input))
            return

    # flood check
    if _text is None:
        allowed, reason = middleware.check_message(user.id, user_row)
        if not allowed:
            if reason == "flood":
                await update.message.reply_text(t('flood'), reply_markup=kb)
            return

    text = _text if _text is not None else update.message.text.strip()
    lower = text.lower()



    # settings
    if lower.strip() in ("⚙️ настройки", "⚙️ settings"):
        inline = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 Сменить город", callback_data="settings_tz")],
        ])
        await update.message.reply_text(t('settings_title'), parse_mode="HTML", reply_markup=inline)
        return

    # stats
    if lower.strip() in ("📊 статистика", "статистика", "📊 statistics", "statistics"):
        await cmd_stats(update, context)
        return

    # show notes
    if any(kw in lower for kw in INTENT_NOTES_LIST):
        await show_notes(update, user.id)
        return

    # save note
    m_note_add = INTENT_NOTES_ADD.match(text)
    if m_note_add:
        note_text = m_note_add.group(1).strip()
        if len(note_text) > 500:
            await update.message.reply_text(t('note_too_long'), reply_markup=kb)
            return
        if await _run(db.get_notes_count, user.id) >= config.MAX_NOTES:
            await update.message.reply_text(t('limit_notes', n=config.MAX_NOTES), reply_markup=kb)
            return
        db.add_note(user.id, note_text)
        await update.message.reply_text(t('note_saved', text=note_text), reply_markup=kb)
        return

    # delete note text
    m_note_del = INTENT_NOTES_DELETE.match(text)
    if m_note_del:
        query_text = m_note_del.group(1).strip().lower()
        notes = db.get_notes(user.id)
        matched = [n for n in notes if query_text in n["text"].lower()]
        if not matched:
            await update.message.reply_text(t('note_not_found', query=query_text), reply_markup=kb)
        elif len(matched) == 1:
            db.delete_note(matched[0]["id"], user.id)
            await update.message.reply_text(t('note_deleted', text=matched[0]['text']), reply_markup=kb)
        else:
            lines = [t('note_clarify')]
            for n in matched:
                lines.append(f"• {n['text']}")
            await update.message.reply_text("\n".join(lines), reply_markup=kb)
        return

    # delete by index
    m_ord = INTENT_DELETE_ORDINAL.search(lower)
    if m_ord:
        ordinal_word = m_ord.group(3).lower()
        idx = ORDINAL_TO_INDEX.get(ordinal_word)
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text(t('none_to_delete'), reply_markup=kb)
            return
        try:
            target = reminders[idx]
        except IndexError:
            await update.message.reply_text(t('ordinal_none'), reply_markup=kb)
            return
        sched.remove_job(target["id"], target["type"])
        db.delete_reminder(target["id"], user.id)
        await update.message.reply_text(t('deleted', msg=target['message']), reply_markup=kb)
        return

    # delete all
    if any(kw in lower for kw in INTENT_DELETE_ALL):
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text(t('none_to_delete'), reply_markup=kb)
            return
        confirm_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(t('btn_yes'), callback_data="delall_yes"),
            InlineKeyboardButton(t('btn_no'), callback_data="delall_no"),
        ]])
        await update.message.reply_text(
            t('confirm_delete_all', n=len(reminders)),
            reply_markup=confirm_kb,
        )
        return


    # list reminders
    if any(kw in lower for kw in INTENT_LIST):
        await show_reminders(update, user.id, user_row["timezone"] if user_row else None)
        return

    # delete by number
    m = INTENT_DELETE_N.search(lower)
    if m:
        rid = int(next(g for g in m.groups()[2:] if g is not None))
        all_r = db.get_reminders(user.id)
        target = next((r for r in all_r if r["id"] == rid), None)
        if target:
            sched.remove_job(rid, target["type"])
            db.delete_reminder(rid, user.id)
            await update.message.reply_text(t('deleted', msg=target['message']), reply_markup=kb)
        else:
            await update.message.reply_text(t('not_found_id', id=rid), reply_markup=kb)
        return

    # greeting
    _has_reminder_intent = re.search(r"напомни|напоминай|через\s+\d|через\s+[а-я]", lower)
    if not _has_reminder_intent and re.search(
        r"^\s*(привет|прив|приветик|хай|хей|здравствуй|здарова|здорово|"
        r"добрый|доброе|доброго|хелло|салам|ку|кук|йоу|yo|ey|эй|"
        r"hi|hey|hello|sup|wassup|хола|hola|бонжур|чё|чо|"
        r"дарова|даров|дратути|ворлд|world)\b",
        lower
    ):
        import random
        replies_ru = [
            "Привет! Напиши что и когда напомнить 👇",
            "Хей! Чё напомнить?",
            "Салам! Говори — что и когда 🕐",
            "Йоу! Ставим напоминание?",
            "Привет-привет 👋 Пиши что напомнить",
        ]
        replies = replies_ru
        await update.message.reply_text(random.choice(replies), reply_markup=kb)
        return

    # short replies
    _suffix = r"(\s+(бро|бра|брат|друг|чел|ман|дружище|чувак|красавчик|топ|👍|🙏))?\s*$"
    if re.search(
        r"^\s*(спасиб\w*|пасиб\w*|спс|сяп|благодарю|thanks|thank you|ок|окей|ok|okay|"
        r"понял|поняла|понятно|хорошо|супер|отлично|класс|👍|🙏|💪|огонь|"
        r"норм|нормально|ладно|договорились|давай|пока|до свидания|чао|bye)" + _suffix,
        lower
    ):
        await update.message.reply_text("👍", reply_markup=kb)
        return

    # parse reminder
    user_tz = user_row["timezone"] if user_row else None
    # use EN format if message is in EN
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    expanded = []
    for line in lines:
        parts = re.split(r',?\s+(?:а|и)\s+(?=через\s)', line, flags=re.IGNORECASE)
        if len(parts) > 1:
            expanded.append(parts[0])
            has_trigger = bool(re.search(r'\bнапомни\b|\bнапоминай\b', parts[0], re.IGNORECASE))
            for p in parts[1:]:
                p = p.strip()
                if has_trigger and not re.search(r'\bнапомни\b|\bнапоминай\b', p, re.IGNORECASE):
                    p = "напомни " + p
                expanded.append(p)
        else:
            expanded.append(line)
    lines = expanded

    results = []
    for line in lines:
        parsed = reminder_parser.parse(line, user_tz=user_tz)
        if not parsed["error"]:
            results.append((line, parsed))

    if not results:
        parsed = reminder_parser.parse(lines[0] if lines else text, user_tz=user_tz)
        error_msg = parsed["error"]
        await update.message.reply_text(error_msg, reply_markup=kb)
        return

    ok, err = middleware.check_message_length(text)
    if not ok:
        await update.message.reply_text(err, reply_markup=kb)
        return

    bot = context.bot
    reply_lines = []
    created_ids = []
    movable_ids = set()

    for _, parsed in results:
        ok, err = middleware.check_new_reminder(user.id)
        if not ok:
            # часть напоминаний уже создана — человек должен об этом узнать
            tail = (chr(10).join(reply_lines) + " ✅" + chr(10) + chr(10)) if reply_lines else ""
            await update.message.reply_text(tail + err, reply_markup=kb)
            return

        reminder_id = await _run(db.add_reminder,
            user.id, update.effective_chat.id, parsed["message"],
            parsed["type"], parsed.get("interval_seconds"), parsed.get("next_fire"),
            parsed.get("days_of_week"), parsed.get("at_time"), parsed.get("until"),
            bool(parsed.get("wall_clock")),
        )
        await _run(db.increment_reminders_created, user.id)
        created_ids.append(reminder_id)
        if not parsed.get("days_of_week"):
            movable_ids.add(reminder_id)
        until_str = ""
        if parsed.get("until"):
            until_dt = _local_dt(parsed["until"], user.id, user_tz)
            until_str = t('until_suffix', date=until_dt.strftime('%d.%m'))

        if parsed.get("days_of_week"):
            sched.add_cron_job(bot, reminder_id, update.effective_chat.id, parsed["message"],
                               parsed["days_of_week"], parsed["at_time"], user_tz, parsed.get("until"))
            days_str = t('days_weekdays' if parsed["days_of_week"] == "mon-fri" else 'days_weekend')
            reply_lines.append(
                t('confirm_days', days=days_str, time=parsed["at_time"], msg=parsed['message']) + until_str
            )

        elif parsed["type"] == "recurring":
            start_date = None
            if parsed.get("next_fire"):
                start_date = datetime.fromtimestamp(parsed["next_fire"], tz=timezone.utc)
            sched.add_recurring_job(bot, reminder_id, update.effective_chat.id, parsed["message"],
                                    parsed["interval_seconds"], start_date=start_date,
                                    until=parsed.get("until"))
            interval_str = format_interval(parsed["interval_seconds"])
            if start_date:
                dt = _local_dt(parsed["next_fire"], user.id, user_tz)
                reply_lines.append(t('confirm_recurring_from', interval=interval_str, time=_fmt_time(dt), msg=parsed['message']) + until_str)
            else:
                reply_lines.append(t('confirm_recurring', interval=interval_str, msg=parsed['message']) + until_str)

        elif parsed["type"] == "once":
            sched.add_once_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["next_fire"])
            dt = _local_dt(parsed["next_fire"], user.id, user_tz)
            reply_lines.append(t('confirm_once', time=_fmt_datetime(dt), msg=parsed['message']))

    tz_warning = ""
    if user_row and user_row.get("timezone", "UTC") == "UTC":
        tz_warning = "\n\n" + t('tz_missing')
        context.user_data["awaiting_city"] = True

    rows = []
    for n, rid in enumerate(created_ids, 1):
        # номер в подписи нужен только когда напоминаний несколько
        suffix = f" {n}" if len(created_ids) > 1 else ""
        row = []
        if rid in movable_ids:
            row.append(InlineKeyboardButton(t('btn_move_n', n="").strip() + suffix,
                                            callback_data=f"move_{rid}"))
        row.append(InlineKeyboardButton(t('btn_delete_n', n="").strip() + suffix,
                                        callback_data=f"delx_{rid}"))
        rows.append(row)
    await update.message.reply_text("\n".join(reply_lines) + " ✅" + tz_warning,
                                    reply_markup=InlineKeyboardMarkup(rows))

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)

    if user_row and user_row["is_banned"]:
        return

    allowed, reason = middleware.check_message(user.id, user_row)
    if not allowed:
        if reason == "flood":
            await update.message.reply_text(t('flood'), reply_markup=get_keyboard())
        return

    voice = update.message.voice
    if voice.duration and voice.duration > 120:
        await update.message.reply_text(t('voice_too_long'), reply_markup=get_keyboard())
        return
    file = await context.bot.get_file(voice.file_id)
    audio_bytes = await file.download_as_bytearray()

    try:
        client = Groq(api_key=config.GROQ_API_KEY)
        transcription = client.audio.transcriptions.create(
            file=("voice.ogg", bytes(audio_bytes)),
            model="whisper-large-v3",
            language="ru",
        )
        text = transcription.text.strip()
    except Exception as e:
        logger.error(f"Groq transcription error: {e}")
        await update.message.reply_text(t('voice_fail'), reply_markup=get_keyboard())
        return

    if not text:
        await update.message.reply_text(t('voice_fail'), reply_markup=get_keyboard())
        return

    ok, err = middleware.check_message_length(text)
    if not ok:
        await update.message.reply_text(err, reply_markup=get_keyboard())
        return

    await handle_message(update, context, _text=text)

def init_db_with_retry(attempts: int = 5, delay: int = 5):
    for attempt in range(1, attempts + 1):
        try:
            db.init_db()
            return
        except psycopg2.Error as e:
            logger.error(f"База недоступна (попытка {attempt}): {e}")
            if attempt == attempts:
                raise
            _time.sleep(delay)

def main():
    init_db_with_retry()

    app = Application.builder().token(config.TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("admin", admin_handlers.cmd_admin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    async def on_error(update, context):
        logger.error("Ошибка обработки апдейта", exc_info=context.error)
        chat = update.effective_chat if isinstance(update, Update) else None
        # обрывы связи с телеграмом лечатся ретраем, дёргать юзера незачем
        if not chat or isinstance(context.error, NetworkError):
            return
        try:
            await context.bot.send_message(chat_id=chat.id, text=t('error_generic'))
        except Exception:
            pass

    app.add_error_handler(on_error)

    async def on_startup(app):
        sched.restore_jobs(app.bot)
        sched.add_maintenance_job()
        sched.scheduler.start()
        logger.info("Бот запущен, напоминания восстановлены.")

    async def on_shutdown(app):
        sched.scheduler.shutdown()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    logger.info("Запускаю бота...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()