import logging
import re
import time as _time
from datetime import datetime, timezone
import zoneinfo

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
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

def get_lang(user_row) -> str:
    return (user_row.get('language') or 'ru') if user_row else 'ru'

def get_keyboard(lang: str):
    return ReplyKeyboardMarkup(
        [[t(lang, 'btn_reminders'), t(lang, 'btn_stats')], [t(lang, 'btn_notes')]],
        resize_keyboard=True,
        input_field_placeholder=t(lang, 'placeholder'),
    )

def format_interval(seconds: int, lang: str = 'ru') -> str:
    if seconds < 60:
        return t(lang, 'interval_sec', n=seconds)
    if seconds < 3600:
        return t(lang, 'interval_min', n=seconds // 60)
    h = seconds / 3600
    return t(lang, 'interval_h', n=int(h) if h == int(h) else f"{h:.1f}")

INTENT_NOTES_LIST = [
    "мои заметки", "покажи заметки", "все заметки", "заметки",
    "🗒 заметки", "список заметок", "покажи все заметки",
    "🗒 notes", "notes", "my notes", "show notes", "all notes",
]

INTENT_NOTES_ADD = re.compile(
    r"^\s*(?:запомни(?:\s+что)?\s*:?\s+|remember\s*:\s*)(.+)$",
    re.IGNORECASE | re.DOTALL
)

INTENT_NOTES_DELETE = re.compile(
    r"^\s*(?:забудь\s+про|forget\s+about)\s+(.+)$",
    re.IGNORECASE | re.DOTALL
)

PENDING_AMPM: dict[int, str] = {}  # user_id → original reminder text awaiting am/pm

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
    "📋 my reminders", "my reminders", "reminders", "show reminders",
    "list reminders", "active reminders",
]

INTENT_DELETE_ALL = [
    "удали все", "удалить все", "убери все", "удали всё",
    "удалить всё", "очисти все", "очисти всё", "удали все напоминания",
    "убери все напоминания", "сотри все", "сотри всё",
    "сотри все напоминания", "удалить все напоминания",
    "очисти список", "снеси все", "снеси всё",
    "delete all", "remove all", "clear all", "delete all reminders",
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

INTENT_DELETE_LAST_EN = re.compile(
    r"\b(delete|remove|cancel|drop)\s+(the\s+)?last(\s+reminder)?\b",
    re.IGNORECASE
)

INTENT_DELETE_N = re.compile(
    r"(удали|удалить|убери|убрать|отмени|отменить)\s+"
    r"(напоминание\s+)?(?:#?(\d+)|номер\s+(\d+)|под\s+номером\s+(\d+))"
)

INTENT_LANG_CHANGE = re.compile(
    r"(поменять|поменяй|смени|сменить|изменить|измени)\s+язык"
    r"|язык\s+(поменять|сменить|изменить)"
    r"|хочу\s+(поменять|сменить)\s+язык"
    r"|change\s+lang(uage)?"
    r"|switch\s+lang(uage)?"
    r"|set\s+lang(uage)?",
    re.IGNORECASE
)

def _get_user_tz(user_id: int) -> zoneinfo.ZoneInfo:
    user = db.get_user(user_id)
    tz_name = user["timezone"] if user else "UTC"
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")

def _local_dt(ts: float, user_id: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=_get_user_tz(user_id))

def _fmt_time(dt: datetime, lang: str) -> str:
    if lang == 'en':
        return dt.strftime('%I:%M %p').lstrip('0')
    return dt.strftime('%H:%M')

def _fmt_datetime(dt: datetime, lang: str) -> str:
    if lang == 'en':
        return dt.strftime('%I:%M %p %d.%m').lstrip('0')
    return dt.strftime('%H:%M %d.%m')

def build_reminders_message(reminders, user_id: int, lang: str):
    lines = [t(lang, 'reminders_header')]
    buttons = []
    for r in reminders:
        if r["type"] == "recurring":
            interval_str = format_interval(r["interval_seconds"], lang)
            label = t(lang, 'label_recurring', interval=interval_str, msg=r['message'])
        else:
            dt = _local_dt(r["next_fire"], user_id)
            label = t(lang, 'label_once', time=_fmt_datetime(dt, lang), msg=r['message'])
        lines.append(f"• {label}")
        buttons.append([InlineKeyboardButton(
            t(lang, 'btn_delete', msg=r['message'][:30]),
            callback_data=f"del_{r['id']}"
        )])
    buttons.append([InlineKeyboardButton(t(lang, 'btn_close'), callback_data="close")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)

async def show_notes(update: Update, user_id: int, lang: str):
    notes = db.get_notes(user_id)
    kb = get_keyboard(lang)
    if not notes:
        await update.message.reply_text(t(lang, 'no_notes'), reply_markup=kb)
        return
    lines = [t(lang, 'notes_header')]
    buttons = []
    for n in notes:
        lines.append(f"• {n['text']}")
        buttons.append([InlineKeyboardButton(f"🗑 {n['text'][:40]}", callback_data=f"delnote_{n['id']}")])
    buttons.append([InlineKeyboardButton(t(lang, 'btn_close'), callback_data="close")])
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
    lang = get_lang(user_row)
    tz_set = user_row and user_row["timezone"] != "UTC"

    if is_new or not tz_set:
        context.user_data["awaiting_city"] = True
        await update.message.reply_text(t(lang, 'welcome_new', name=user.first_name))
        return

    await update.message.reply_text(
        t(lang, 'welcome_back', name=user.first_name),
        reply_markup=get_keyboard(lang),
    )

async def _send_welcome(update: Update, lang: str):
    await update.message.reply_text(
        t(lang, 'welcome_features'),
        reply_markup=get_keyboard(lang),
    )

async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")
    user_row = db.get_user(user.id)
    lang = get_lang(user_row)
    context.user_data["awaiting_city"] = True
    await update.message.reply_text(t(lang, 'awaiting_city'))

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")
    user_row = db.get_user(user.id)
    lang = get_lang(user_row)
    stats = db.get_user_stats(user.id)
    await update.message.reply_text(
        t(lang, 'stats', total=stats['total_created'], active=stats['active']),
        parse_mode="HTML", reply_markup=get_keyboard(lang)
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def show_reminders(update: Update, user_id: int, lang: str):
    reminders = db.get_reminders(user_id)
    if not reminders:
        await update.message.reply_text(t(lang, 'no_reminders'), reply_markup=get_keyboard(lang))
        return
    text, keyboard = build_reminders_message(reminders, user_id, lang)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_row = db.get_user(query.from_user.id)
    lang = get_lang(user_row)

    # lang switch
    if query.data.startswith("lang_"):
        new_lang = query.data[5:]
        db.update_language(query.from_user.id, new_lang)
        await query.edit_message_text(t(new_lang, 'lang_changed'))
        await context.bot.send_message(
            query.message.chat_id,
            t(new_lang, 'welcome_back', name=query.from_user.first_name or ""),
            reply_markup=get_keyboard(new_lang),
        )
        return

    # close
    if query.data == "close":
        await query.delete_message()
        return

    # snooze
    if query.data.startswith("snooze_"):
        _, rid_str, mins_str = query.data.split("_")
        reminder_id = int(rid_str)
        minutes = int(mins_str)
        info = sched.PENDING_SNOOZE.get(reminder_id)
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
            dt = _local_dt(new_fire, query.from_user.id)
            await query.edit_message_text(t(lang, 'snoozed', msg=info['message'], time=_fmt_time(dt, lang)))
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
            lines = [t(lang, 'notes_header')]
            buttons = []
            for n in notes:
                lines.append(f"• {n['text']}")
                buttons.append([InlineKeyboardButton(f"🗑 {n['text'][:40]}", callback_data=f"delnote_{n['id']}")])
            buttons.append([InlineKeyboardButton(t(lang, 'btn_close'), callback_data="close")])
            await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.edit_message_text(t(lang, 'notes_all_deleted'))
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
            text, keyboard = build_reminders_message(remaining, user_id, lang)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.edit_message_text(t(lang, 'all_deleted', count=0).split("(")[0].strip())
    else:
        await query.edit_message_text(t(lang, 'already_deleted'))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, _text: str = None):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
    lang = get_lang(user_row)
    kb = get_keyboard(lang)

    if user_row and user_row["is_banned"]:
        return

    # waiting for city
    if context.user_data.get("awaiting_city"):
        city_input = (update.message.text or "").strip()
        await update.message.reply_text(t(lang, 'city_searching'))
        tz, display = city_tz.city_to_timezone(city_input)
        if tz:
            db.update_timezone(user.id, tz)
            context.user_data.pop("awaiting_city", None)
            await update.message.reply_text(t(lang, 'city_set', display=display), reply_markup=kb)
            await _send_welcome(update, lang)
        else:
            await update.message.reply_text(t(lang, 'city_not_found', city=city_input))
        return

    # flood check
    if _text is None:
        allowed, reason = middleware.check_message(user.id)
        if not allowed:
            if reason == "flood":
                await update.message.reply_text(t(lang, 'flood'), reply_markup=kb)
            return

    text = _text if _text is not None else update.message.text.strip()
    lower = text.lower()

    # AM/PM reply
    _is_ampm_reply = bool(re.match(r'^\s*(am|pm)(\s+\w{0,15})?\s*$', lower, re.IGNORECASE))
    if _is_ampm_reply or user.id in PENDING_AMPM:
        m_ap = _AMPM_REPLY.search(lower)
        if m_ap:
            if user.id in PENDING_AMPM:
                original = PENDING_AMPM.pop(user.id)
                suffix = m_ap.group(1).upper()
                fixed = re.sub(r'(\bat\s+\d{1,2})\b(?!\s*(?:am|pm|:\d))', rf'\1 {suffix}', original, flags=re.IGNORECASE)
                await handle_message(update, context, _text=fixed)
            else:
                await update.message.reply_text(
                    "I lost your reminder after a restart — please send it again with AM or PM.\n"
                    "E.g.: «remind me at 8 PM to stretch»",
                    reply_markup=kb
                )
            return

    # lang switch
    if INTENT_LANG_CHANGE.search(lower):
        inline_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ]])
        await update.message.reply_text(t(lang, 'lang_ask'), reply_markup=inline_kb)
        return

    # stats
    if lower.strip() in ("📊 статистика", "статистика", "📊 statistics", "statistics"):
        await cmd_stats(update, context)
        return

    # show notes
    if any(kw in lower for kw in INTENT_NOTES_LIST):
        await show_notes(update, user.id, lang)
        return

    # save note
    m_note_add = INTENT_NOTES_ADD.match(text)
    if m_note_add:
        note_text = m_note_add.group(1).strip()
        if len(note_text) > 500:
            await update.message.reply_text(t(lang, 'note_too_long'), reply_markup=kb)
            return
        db.add_note(user.id, note_text)
        await update.message.reply_text(t(lang, 'note_saved', text=note_text), reply_markup=kb)
        return

    # delete note text
    m_note_del = INTENT_NOTES_DELETE.match(text)
    if m_note_del:
        query_text = m_note_del.group(1).strip().lower()
        notes = db.get_notes(user.id)
        matched = [n for n in notes if query_text in n["text"].lower()]
        if not matched:
            await update.message.reply_text(t(lang, 'note_not_found', query=query_text), reply_markup=kb)
        elif len(matched) == 1:
            db.delete_note(matched[0]["id"], user.id)
            await update.message.reply_text(t(lang, 'note_deleted', text=matched[0]['text']), reply_markup=kb)
        else:
            lines = [t(lang, 'note_clarify')]
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
            await update.message.reply_text(t(lang, 'none_to_delete'), reply_markup=kb)
            return
        try:
            target = reminders[idx]
        except IndexError:
            await update.message.reply_text(t(lang, 'ordinal_none'), reply_markup=kb)
            return
        sched.remove_job(target["id"], target["type"])
        db.delete_reminder(target["id"], user.id)
        await update.message.reply_text(t(lang, 'deleted', msg=target['message']), reply_markup=kb)
        return

    # delete all
    if any(kw in lower for kw in INTENT_DELETE_ALL):
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text(t(lang, 'none_to_delete'), reply_markup=kb)
            return
        for r in reminders:
            sched.remove_job(r["id"], r["type"])
            db.delete_reminder(r["id"], user.id)
        await update.message.reply_text(t(lang, 'all_deleted', count=len(reminders)), reply_markup=kb)
        return

    # delete last EN
    if INTENT_DELETE_LAST_EN.search(lower):
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text(t(lang, 'none_to_delete'), reply_markup=kb)
            return
        last = reminders[-1]
        sched.remove_job(last["id"], last["type"])
        db.delete_reminder(last["id"], user.id)
        await update.message.reply_text(t(lang, 'last_deleted', msg=last['message']), reply_markup=kb)
        return

    # list reminders
    if any(kw in lower for kw in INTENT_LIST):
        await show_reminders(update, user.id, lang)
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
            await update.message.reply_text(t(lang, 'deleted', msg=target['message']), reply_markup=kb)
        else:
            await update.message.reply_text(t(lang, 'not_found_id', id=rid), reply_markup=kb)
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
        replies_en = [
            "Hey! What should I remind you about? 👇",
            "Hi! What do you need a reminder for?",
            "Hello! Just say what to remind you and when 🕐",
            "Hey there 👋 What's the reminder?",
        ]
        replies = replies_en if lang == 'en' else replies_ru
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
        if error_msg and "AM or PM" in error_msg:
            PENDING_AMPM[user.id] = lines[0] if lines else text
        await update.message.reply_text(error_msg, reply_markup=kb)
        return

    ok, err = middleware.check_message_length(text)
    if not ok:
        await update.message.reply_text(err, reply_markup=kb)
        return

    bot = context.bot
    reply_lines = []
    created_ids = []

    for _, parsed in results:
        ok, err = middleware.check_new_reminder(user.id)
        if not ok:
            await update.message.reply_text(err, reply_markup=kb)
            return

        reminder_id = db.add_reminder(
            user_id=user.id,
            chat_id=update.effective_chat.id,
            message=parsed["message"],
            type_=parsed["type"],
            interval_seconds=parsed.get("interval_seconds"),
            next_fire=parsed.get("next_fire"),
        )
        db.increment_reminders_created(user.id)
        created_ids.append(reminder_id)

        if parsed["type"] == "recurring":
            start_date = None
            if parsed.get("next_fire"):
                start_date = datetime.fromtimestamp(parsed["next_fire"], tz=timezone.utc)
            sched.add_recurring_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["interval_seconds"], start_date=start_date)
            interval_str = format_interval(parsed["interval_seconds"], lang)
            if start_date:
                dt = _local_dt(parsed["next_fire"], user.id)
                reply_lines.append(t(lang, 'confirm_recurring_from', interval=interval_str, time=_fmt_time(dt, lang), msg=parsed['message']))
            else:
                reply_lines.append(t(lang, 'confirm_recurring', interval=interval_str, msg=parsed['message']))

        elif parsed["type"] == "once":
            sched.add_once_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["next_fire"])
            sched.add_once_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["next_fire"])
            dt = _local_dt(parsed["next_fire"], user.id)
            reply_lines.append(t(lang, 'confirm_once', time=_fmt_datetime(dt, lang), msg=parsed['message']))

    tz_warning = ""
    if user_row and user_row.get("timezone", "UTC") == "UTC":
        tz_warning = "\n\n⚠️ Город не задан — время показано в UTC. Напиши /timezone чтобы указать свой город." if lang == "ru" \
               else "\n\n⚠️ City not set — time shown in UTC. Type /timezone to set your city."

    del_label = "🗑 Удалить" if lang == "ru" else "🗑 Delete"
    confirm_inline = InlineKeyboardMarkup([
        [InlineKeyboardButton(del_label, callback_data=f"del_{rid}")] for rid in created_ids
    ])
    await update.message.reply_text("\n".join(reply_lines) + " ✅" + tz_warning, reply_markup=confirm_inline)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
    lang = get_lang(user_row)

    if user_row and user_row["is_banned"]:
        return

    voice = update.message.voice
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
        await update.message.reply_text(t(lang, 'voice_fail'), reply_markup=get_keyboard(lang))
        return

    if not text:
        await update.message.reply_text(t(lang, 'voice_fail'), reply_markup=get_keyboard(lang))
        return

    await handle_message(update, context, _text=text)

def main():
    db.init_db()

    app = Application.builder().token(config.TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("timezone", cmd_timezone))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("admin", admin_handlers.cmd_admin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    async def on_startup(app):
        sched.restore_jobs(app.bot)
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
