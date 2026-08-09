import html
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

import config
import db
import scheduler as sched

def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

HELP = (
    "<b>Админка</b>\n\n"
    "/admin — сводка\n"
    "/admin users — список пользователей\n"
    "/admin users 50 — сколько строк показать\n"
    "/admin ban USER_ID\n"
    "/admin unban USER_ID"
)

def _date(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%y")

def format_overview(d: dict, jobs: int) -> str:
    if d["users"]:
        share = round(d["active_week"] / d["users"] * 100)
        avg = round(d["reminders"] / d["users"], 1)
    else:
        share = avg = 0
    return (
        "📊 <b>Сводка</b>\n\n"
        "<b>Пользователи</b>\n"
        f"• Всего: {d['users']}\n"
        f"• Активны за неделю: {d['active_week']} ({share}%)\n"
        f"• Активны сегодня: {d['active_today']}\n"
        f"• Новых за сутки: {d['new_day']}, за неделю: {d['new_week']}\n"
        f"• Забанено: {d['banned']}\n\n"
        "<b>Напоминания</b>\n"
        f"• Сейчас активных: {d['reminders']} (в среднем {avg} на человека)\n"
        f"• Разовых: {d['once']}\n"
        f"• Повторяющихся: {d['recurring']}\n"
        f"• По дням недели: {d['by_days']}\n"
        f"• Заметок: {d['notes']}\n\n"
        "<b>Создано</b>\n"
        f"• За всё время: {d['created_total']}\n"
        f"• Сегодня: {d['created_today']}\n\n"
        f"<b>Задач в планировщике:</b> {jobs}"
    )

def format_users(rows) -> str:
    if not rows:
        return "Пользователей пока нет."
    lines = ["👥 <b>Пользователи</b>", "<i>создано / активных · последний раз · регистрация</i>", ""]
    for r in rows:
        name = r["username"] and f"@{r['username']}" or (r["first_name"] or str(r["user_id"]))
        mark = " 🚫" if r["is_banned"] else ""
        last = r["last_day"].strftime("%d.%m.%y") if r["last_day"] else "—"
        lines.append(
            f"<code>{r['user_id']}</code> {html.escape(name)}{mark}\n"
            f"   {r['reminders_created']} / {r['active']} · {last} · {_date(r['registered_at'])}"
        )
    return "\n".join(lines)

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    cmd = args[0].lower() if args else "stats"

    if cmd in ("stats", "help"):
        if cmd == "help":
            await update.message.reply_text(HELP, parse_mode="HTML")
            return
        overview = db.get_admin_overview()
        text = format_overview(overview, len(sched.scheduler.get_jobs()))
        await update.message.reply_text(text + "\n\n/admin help", parse_mode="HTML")

    elif cmd == "users":
        limit = 20
        if len(args) >= 2 and args[1].isdigit():
            limit = min(int(args[1]), 100)
        rows = db.get_user_activity(limit)
        await update.message.reply_text(format_users(rows), parse_mode="HTML")

    elif cmd == "ban" and len(args) >= 2:
        try:
            uid = int(args[1])
            db.ban_user(uid, True)
            await update.message.reply_text(f"✅ Забанен: {uid}")
        except ValueError:
            await update.message.reply_text("Неверный user_id")

    elif cmd == "unban" and len(args) >= 2:
        try:
            uid = int(args[1])
            db.ban_user(uid, False)
            await update.message.reply_text(f"✅ Разбанен: {uid}")
        except ValueError:
            await update.message.reply_text("Неверный user_id")

    else:
        await update.message.reply_text(HELP, parse_mode="HTML")
