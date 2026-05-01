import logging
import re
from datetime import datetime
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📋 Мои напоминания", "📊 Статистика"], ["🗒 Заметки"]],
    resize_keyboard=True,
    input_field_placeholder="Напиши что и когда напомнить..."
)

INTENT_NOTES_LIST = [
    "мои заметки", "покажи заметки", "все заметки", "заметки",
    "🗒 заметки", "список заметок", "покажи все заметки",
]

INTENT_NOTES_ADD = re.compile(
    r"^\s*запомни(?:\s+что)?\s*:?\s+(.+)$", re.IGNORECASE | re.DOTALL
)

INTENT_NOTES_DELETE = re.compile(
    r"^\s*забудь\s+про\s+(.+)$", re.IGNORECASE | re.DOTALL
)

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

def _get_user_tz(user_id: int) -> zoneinfo.ZoneInfo:
    user = db.get_user(user_id)
    tz_name = user["timezone"] if user else "UTC"
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")

def _local_dt(ts: float, user_id: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=_get_user_tz(user_id))

def format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} мин"
    h = seconds / 3600
    if h == int(h):
        return f"{int(h)} ч"
    return f"{h:.1f} ч"

def build_reminders_message(reminders, user_id: int):
    lines = ["<b>Твои напоминания:</b>\n"]
    buttons = []
    for r in reminders:
        if r["type"] == "recurring":
            interval_str = format_interval(r["interval_seconds"])
            label = f"каждые {interval_str} — {r['message']}"
        else:
            dt = _local_dt(r["next_fire"], user_id)
            label = f"в {dt.strftime('%H:%M %d.%m')} — {r['message']}"
        lines.append(f"• {label}")
        buttons.append([InlineKeyboardButton(f"🗑 Удалить — {r['message'][:30]}", callback_data=f"del_{r['id']}")])

    buttons.append([InlineKeyboardButton("✖ Закрыть", callback_data="close")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)

async def show_notes(update: Update, user_id: int):
    notes = db.get_notes(user_id)
    if not notes:
        await update.message.reply_text(
            "У тебя нет заметок.\n\nЧтобы сохранить — напиши: запомни: текст",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    lines = ["<b>Твои заметки:</b>\n"]
    buttons = []
    for n in notes:
        lines.append(f"• {n['text']}")
        buttons.append([InlineKeyboardButton(f"🗑 {n['text'][:40]}", callback_data=f"delnote_{n['id']}")])
    buttons.append([InlineKeyboardButton("✖ Закрыть", callback_data="close")])
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
    tz_set = user_row and user_row["timezone"] != "UTC"

    if is_new or not tz_set:
        context.user_data["awaiting_city"] = True
        await update.message.reply_text(
            f"Привет, {user.first_name}! Я бот-напоминалка. "
            "Напиши свой город, чтобы напоминания приходили в точное время!"
        )
        return

    await update.message.reply_text(
        f"Привет, {user.first_name}! Чем могу помочь?",
        reply_markup=MAIN_KEYBOARD,
    )

async def _send_welcome(update: Update, first_name: str):
    await update.message.reply_text(
        "Вот что я умею:\n\n"
        "⏰ Одноразовые: напомни о встрече завтра в 10, напомни позвонить маме через полчаса\n\n"
        "🔁 Повторяющиеся: напоминай пить воду каждые 2 часа\n\n"
        "🗒 Заметки: запомни: друг должен мне 3000₽ — сохраняю без времени, смотришь когда нужно\n\n"
        "Пиши как удобно — любые напоминания в любом формате.",
        reply_markup=MAIN_KEYBOARD,
    )

async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")
    context.user_data["awaiting_city"] = True
    await update.message.reply_text("Напиши свой город:")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")
    stats = db.get_user_stats(user.id)
    text = (
        "📊 <b>Твоя статистика:</b>\n\n"
        f"• Всего создано: {stats['total_created']}\n"
        f"• Активных сейчас: {stats['active']}"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def show_reminders(update: Update, user_id: int):
    reminders = db.get_reminders(user_id)
    if not reminders:
        await update.message.reply_text(
            "У тебя нет активных напоминаний.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    text, keyboard = build_reminders_message(reminders, user_id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # close
    if query.data == "close":
        await query.delete_message()
        return

    # snooze
    if query.data.startswith("snooze_"):
        import time as _time
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
            await query.edit_message_text(f"⏰ {info['message']}\n\n⏩ Перенесено на {dt.strftime('%H:%M')}")
        else:
            await query.edit_message_reply_markup(reply_markup=None)
        return

    if query.data.startswith("dismiss_"):
        reminder_id = int(query.data.split("_")[1])
        sched.PENDING_SNOOZE.pop(reminder_id, None)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # ── Удаление из списка ────────────────────────────────────────────────────
    if query.data.startswith("delnote_"):
        note_id = int(query.data[8:])
        db.delete_note(note_id, query.from_user.id)
        notes = db.get_notes(query.from_user.id)
        if notes:
            lines = ["<b>Твои заметки:</b>\n"]
            buttons = []
            for n in notes:
                lines.append(f"• {n['text']}")
                buttons.append([InlineKeyboardButton(f"🗑 {n['text'][:40]}", callback_data=f"delnote_{n['id']}")])
            buttons.append([InlineKeyboardButton("✖ Закрыть", callback_data="close")])
            await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.edit_message_text("Все заметки удалены.")
        return

    if not query.data.startswith("del_"):
        return

    rid = int(query.data[4:])
    user_id = query.from_user.id

    all_r = db.get_reminders(user_id)
    target = next((r for r in all_r if r["id"] == rid), None)

    if target:
        sched.remove_job(rid, target["type"])
        db.delete_reminder(rid, user_id)
        remaining = db.get_reminders(user_id)
        if remaining:
            text, keyboard = build_reminders_message(remaining, user_id)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.edit_message_text("Все напоминания удалены.")
    else:
        await query.edit_message_text("Напоминание уже удалено.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, _text: str = None):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)

    if user_row and user_row["is_banned"]:
        return

    # Ожидание ввода города
    if context.user_data.get("awaiting_city"):
        city_input = (update.message.text or "").strip()
        await update.message.reply_text("Ищу город...")
        tz, display = city_tz.city_to_timezone(city_input)
        if tz:
            db.update_timezone(user.id, tz)
            context.user_data.pop("awaiting_city", None)
            await update.message.reply_text(
                f"✅ Город: {display}\nВремя будет по этому городу.",
                reply_markup=MAIN_KEYBOARD,
            )
            await _send_welcome(update, user.first_name)
        else:
            await update.message.reply_text(
                f"Не смог найти «{city_input}». Попробуй написать иначе — например: Москва, Питер, Новосибирск"
            )
        return

    # Антифлуд
    allowed, reason = middleware.check_message(user.id)
    if not allowed:
        if reason == "flood":
            await update.message.reply_text("Не так быстро! Подожди немного.", reply_markup=MAIN_KEYBOARD)
        return

    text = _text if _text is not None else update.message.text.strip()
    lower = text.lower()

    if lower.strip() in ("📊 статистика", "статистика"):
        await cmd_stats(update, context)
        return

    if any(kw in lower for kw in INTENT_NOTES_LIST):
        await show_notes(update, user.id)
        return

    m_note_add = INTENT_NOTES_ADD.match(text)
    if m_note_add:
        note_text = m_note_add.group(1).strip()
        if len(note_text) > 500:
            await update.message.reply_text("Заметка слишком длинная (макс 500 символов).", reply_markup=MAIN_KEYBOARD)
            return
        db.add_note(user.id, note_text)
        await update.message.reply_text(f"📝 Запомнил: «{note_text}»", reply_markup=MAIN_KEYBOARD)
        return

    m_note_del = INTENT_NOTES_DELETE.match(text)
    if m_note_del:
        query_text = m_note_del.group(1).strip().lower()
        notes = db.get_notes(user.id)
        matched = [n for n in notes if query_text in n["text"].lower()]
        if not matched:
            await update.message.reply_text(f"Не нашёл заметку про «{query_text}».", reply_markup=MAIN_KEYBOARD)
        elif len(matched) == 1:
            db.delete_note(matched[0]["id"], user.id)
            await update.message.reply_text(f"Забыл: «{matched[0]['text']}»", reply_markup=MAIN_KEYBOARD)
        else:
            lines = ["Нашёл несколько заметок, уточни какую удалить:"]
            for n in matched:
                lines.append(f"• {n['text']}")
            await update.message.reply_text("\n".join(lines), reply_markup=MAIN_KEYBOARD)
        return

    m_ord = INTENT_DELETE_ORDINAL.search(lower)
    if m_ord:
        ordinal_word = m_ord.group(3).lower()
        idx = ORDINAL_TO_INDEX.get(ordinal_word)
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text("Нет напоминаний для удаления.", reply_markup=MAIN_KEYBOARD)
            return
        try:
            target = reminders[idx]
        except IndexError:
            await update.message.reply_text("Столько напоминаний нет.", reply_markup=MAIN_KEYBOARD)
            return
        sched.remove_job(target["id"], target["type"])
        db.delete_reminder(target["id"], user.id)
        await update.message.reply_text(f"Удалил: «{target['message']}»", reply_markup=MAIN_KEYBOARD)
        return

    if any(kw in lower for kw in INTENT_DELETE_ALL):
        reminders = db.get_reminders(user.id)
        if not reminders:
            await update.message.reply_text("Нет напоминаний для удаления.", reply_markup=MAIN_KEYBOARD)
            return
        for r in reminders:
            sched.remove_job(r["id"], r["type"])
            db.delete_reminder(r["id"], user.id)
        await update.message.reply_text(f"Удалил все напоминания ({len(reminders)} шт.)", reply_markup=MAIN_KEYBOARD)
        return

    if any(kw in lower for kw in INTENT_LIST):
        await show_reminders(update, user.id)
        return

    m = INTENT_DELETE_N.search(lower)
    if m:
        rid = int(next(g for g in m.groups()[2:] if g is not None))
        all_r = db.get_reminders(user.id)
        target = next((r for r in all_r if r["id"] == rid), None)
        if target:
            sched.remove_job(rid, target["type"])
            db.delete_reminder(rid, user.id)
            await update.message.reply_text(f"Удалил: «{target['message']}»", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text(f"Напоминание #{rid} не найдено.", reply_markup=MAIN_KEYBOARD)
        return

    _has_reminder_intent = re.search(r"напомни|напоминай|через\s+\d|через\s+[а-я]", lower)
    if not _has_reminder_intent and re.search(
        r"^\s*(привет|прив|приветик|хай|хей|здравствуй|здарова|здорово|"
        r"добрый|доброе|доброго|хелло|салам|ку|кук|йоу|yo|ey|эй|"
        r"hi|hey|hello|sup|wassup|хола|hola|бонжур|чё|чо|"
        r"дарова|даров|дратути|ворлд|world)\b",
        lower
    ):
        import random
        replies = [
            "Привет! Напиши что и когда напомнить 👇",
            "Хей! Чё напомнить?",
            "Салам! Говори — что и когда 🕐",
            "Йоу! Ставим напоминание?",
            "Привет-привет 👋 Пиши что напомнить",
        ]
        await update.message.reply_text(random.choice(replies), reply_markup=MAIN_KEYBOARD)
        return

    _suffix = r"(\s+(бро|бра|брат|друг|чел|ман|дружище|чувак|красавчик|топ|👍|🙏))?\s*$"
    if re.search(
        r"^\s*(спасиб\w*|пасиб\w*|спс|сяп|благодарю|thanks|thank you|ок|окей|ok|okay|"
        r"понял|поняла|понятно|хорошо|супер|отлично|класс|👍|🙏|💪|огонь|"
        r"норм|нормально|ладно|договорились|давай|пока|до свидания|чао|bye)" + _suffix,
        lower
    ):
        await update.message.reply_text("👍", reply_markup=MAIN_KEYBOARD)
        return

    user_tz = user_row["timezone"] if user_row else None
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Разбиваем "через X ..., а/и через Y ..." на отдельные строки
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
        await update.message.reply_text(parsed["error"], reply_markup=MAIN_KEYBOARD)
        return

    # Проверка длины (по первой распознанной строке)
    ok, err = middleware.check_message_length(text)
    if not ok:
        await update.message.reply_text(err, reply_markup=MAIN_KEYBOARD)
        return

    bot = context.bot
    reply_lines = []

    for _, parsed in results:
        ok, err = middleware.check_new_reminder(user.id)
        if not ok:
            await update.message.reply_text(err, reply_markup=MAIN_KEYBOARD)
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

        if parsed["type"] == "recurring":
            sched.add_recurring_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["interval_seconds"])
            interval_str = format_interval(parsed["interval_seconds"])
            reply_lines.append(f"🔁 Каждые {interval_str}: «{parsed['message']}»")

        elif parsed["type"] == "once":
            sched.add_once_job(bot, reminder_id, update.effective_chat.id, parsed["message"], parsed["next_fire"])
            dt = _local_dt(parsed["next_fire"], user.id)
            reply_lines.append(f"⏰ В {dt.strftime('%H:%M %d.%m')}: «{parsed['message']}»")

    await update.message.reply_text("\n".join(reply_lines) + " ✅", reply_markup=MAIN_KEYBOARD)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.register_user(user.id, user.username or "", user.first_name or "")

    user_row = db.get_user(user.id)
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
        await update.message.reply_text("Не смог распознать голосовое 😕", reply_markup=MAIN_KEYBOARD)
        return

    if not text:
        await update.message.reply_text("Не смог распознать голосовое 😕", reply_markup=MAIN_KEYBOARD)
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
