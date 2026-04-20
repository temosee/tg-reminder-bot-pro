from telegram import Update
from telegram.ext import ContextTypes

import config
import db
import scheduler as sched

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_ID

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "/admin stats\n"
            "/admin ban USER_ID\n"
            "/admin unban USER_ID"
        )
        return

    cmd = args[0].lower()

    if cmd == "stats":
        users = db.get_all_users_count()
        reminders = db.get_total_reminders_count()
        jobs = len(sched.scheduler.get_jobs())
        await update.message.reply_text(
            f"👥 Пользователей: {users}\n"
            f"⏰ Напоминаний в БД: {reminders}\n"
            f"⚙️ Активных jobs: {jobs}"
        )

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
        await update.message.reply_text("Неизвестная команда.")
