"""
Тесты сборки сообщений и планировщика. Запускать:
    python -X utf8 test_bot.py
"""
import asyncio
import os
import time

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ["ADMIN_ID"] = "834815805"
os.environ.setdefault("DATABASE_URL", "postgresql://dummy")

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import admin
import bot
import db
import scheduler as sched

MSK = "Europe/Moscow"


class FakeMessage:
    def __init__(self, text, chat_id=42):
        self.text = text
        self.chat_id = chat_id


def all_callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def all_labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def case_notes_escaped():
    text, _ = bot.build_notes_message([{"id": 1, "text": "5 < 10 & <b>жирный</b>"}], "ru")
    errors = []
    if "&lt;" not in text or "&amp;" not in text:
        errors.append("спецсимволы не экранированы, телеграм отвергнет сообщение")
    if "<b>жирный</b>" in text:
        errors.append("разметка из текста заметки просочилась в сообщение")
    return errors


def case_once_row_movable():
    r = {"id": 7, "type": "once", "message": "вынести мусор",
         "next_fire": time.time() + 3600, "interval_seconds": None}
    text, markup = bot.build_reminders_message([r], 1, "ru", MSK)
    cbs = all_callbacks(markup)
    errors = []
    if "move_7" not in cbs:
        errors.append(f"нет кнопки переноса: {cbs}")
    if "del_7" not in cbs:
        errors.append(f"нет кнопки удаления: {cbs}")
    return errors


def case_interval_wording():
    checks = {
        86400: "каждый день",
        3 * 86400: "каждые 3 дня",
        3600: "каждый час",
        2 * 3600: "каждые 2 часа",
        5 * 3600: "каждые 5 часов",
        60: "каждую минуту",
        30 * 60: "каждые 30 минут",
        604800: "каждую неделю",
        2 * 604800: "каждые 2 недели",
    }
    errors = []
    for seconds, want in checks.items():
        got = bot.format_interval(seconds, "ru")
        if got != want:
            errors.append(f"{seconds}s: want={want!r} got={got!r}")
    en = {86400: "every day", 2 * 86400: "every 2 days", 3600: "every hour"}
    for seconds, want in en.items():
        got = bot.format_interval(seconds, "en")
        if got != want:
            errors.append(f"{seconds}s (en): want={want!r} got={got!r}")
    return errors


def case_list_text_not_truncated():
    long_msg = "позвонить в поликлинику и записаться к стоматологу на среду"
    rows = [
        {"id": 1, "type": "recurring", "message": long_msg, "next_fire": time.time() + 600,
         "interval_seconds": 86400},
        {"id": 2, "type": "once", "message": long_msg, "next_fire": time.time() + 600,
         "interval_seconds": None},
    ]
    text, markup = bot.build_reminders_message(rows, 1, "ru", MSK)
    errors = []
    if text.count(long_msg) != 2:
        errors.append("текст напоминания обрезан или отсутствует в сообщении")
    if "каждые 1 день" in text:
        errors.append("кривая форма «каждые 1 день»")
    if "каждый день" not in text:
        errors.append(f"не видно интервала: {text}")
    labels = all_labels(markup)
    if not any("Перенести 1" in l for l in labels):
        errors.append(f"нет подписанной кнопки переноса: {labels}")
    if not any("Удалить 2" in l for l in labels):
        errors.append(f"нет подписанной кнопки удаления: {labels}")
    return errors


def case_weekday_row_not_movable():
    r = {"id": 8, "type": "recurring", "message": "зарядка", "next_fire": None,
         "interval_seconds": None, "days_of_week": "mon-fri", "at_time": "09:00"}
    text, markup = bot.build_reminders_message([r], 1, "ru", MSK)
    cbs = all_callbacks(markup)
    errors = []
    if "move_8" in cbs:
        errors.append("предложен перенос напоминания по дням недели")
    if "del_8" not in cbs:
        errors.append("нет кнопки удаления")
    if "будням" not in text:
        errors.append(f"в списке не видно дней недели: {text}")
    if "09:00" not in text:
        errors.append(f"в списке не видно времени: {text}")
    return errors


VLA, MSK_TZ = "Asia/Vladivostok", "Europe/Moscow"


def case_wall_clock_detected():
    import parser as p
    checks = {
        "напомни через 4 часа позвонить": False,
        "напомни завтра в 9:00 позвонить": True,
        "напомни завтра утром позвонить": True,
        "напоминай каждые 2 часа пить воду": False,
        "напоминай каждый день в 9:00 таблетки": True,
        "напоминай по будням в 9:00 зарядка": True,
    }
    errors = []
    for text, want in checks.items():
        got = bool(p.parse(text, user_tz=VLA)["wall_clock"])
        if got != want:
            errors.append(f"{text!r}: ожидалось wall_clock={want}, получено {got}")
    return errors


def case_wall_clock_keeps_the_hour():
    # 9:00 во Владивостоке должно стать 9:00 в Москве, а не 2:00
    fire = datetime(2026, 9, 1, 9, 0, tzinfo=ZoneInfo(VLA)).timestamp()
    shifted = sched.shift_wall_clock(fire, VLA, MSK_TZ)
    got = datetime.fromtimestamp(shifted, ZoneInfo(MSK_TZ))
    if (got.hour, got.minute) != (9, 0):
        return [f"время на часах не сохранилось: {got:%H:%M}"]
    if got.date() != date(2026, 9, 1):
        return [f"уехала дата: {got.date()}"]
    return []


def case_duration_keeps_the_moment():
    # «через 4 часа» — момент не должен двигаться при смене города
    fire = time.time() + 4 * 3600
    rows = [{"id": 1, "type": "once", "message": "позвонить", "next_fire": fire,
             "interval_seconds": None, "wall_clock": False, "days_of_week": None}]
    saved = (db.get_reminders, db.update_next_fire)
    changed = []
    db.get_reminders = lambda uid: rows
    db.update_next_fire = lambda rid, nf: changed.append((rid, nf))
    try:
        moved = sched.reschedule_user(None, 1, VLA, MSK_TZ)
    finally:
        db.get_reminders, db.update_next_fire = saved
    errors = []
    if changed:
        errors.append("напоминание по длительности сдвинули, хотя не должны были")
    if moved:
        errors.append(f"посчитано перенесённых: {moved}, ожидался 0")
    return errors


def case_wall_clock_reminder_moves_with_city():
    fire = datetime.now(ZoneInfo(VLA)).replace(microsecond=0) + timedelta(days=2)
    rows = [{"id": 5, "type": "once", "message": "к врачу", "next_fire": fire.timestamp(),
             "interval_seconds": None, "wall_clock": True, "days_of_week": None, "chat_id": 42}]
    saved = (db.get_reminders, db.update_next_fire)
    changed = []
    db.get_reminders = lambda uid: rows
    db.update_next_fire = lambda rid, nf: changed.append((rid, nf))
    try:
        sched.reschedule_user(None, 1, VLA, MSK_TZ)
    finally:
        db.get_reminders, db.update_next_fire = saved
        sched.remove_job(5, "once")
    if not changed:
        return ["напоминание по часам не переехало вместе с городом"]
    got = datetime.fromtimestamp(changed[0][1], ZoneInfo(MSK_TZ))
    if (got.hour, got.minute) != (fire.hour, fire.minute):
        return [f"час не сохранился: было {fire:%H:%M}, стало {got:%H:%M}"]
    return []


def case_move_menu_offsets():
    markup = bot.build_move_menu(5, "ru")
    cbs = all_callbacks(markup)
    expected = [f"mv_5_{m}" for m in (15, 30, 60, 180, 360, 1440)]
    missing = [c for c in expected if c not in cbs]
    errors = []
    if missing:
        errors.append(f"нет вариантов переноса: {missing}")
    if "back_list" not in cbs:
        errors.append("нет кнопки возврата к списку")
    labels = all_labels(markup)
    # «Завтра» тут читается двусмысленно, если напоминание и так на завтра
    if any("Завтра" in l for l in labels):
        errors.append(f"в меню переноса осталось «Завтра»: {labels}")
    if not any("+1 день" in l for l in labels):
        errors.append(f"нет варианта «+1 день»: {labels}")
    return errors


def case_snooze_recovered_after_restart():
    info = bot.recover_snooze_info(FakeMessage("⏰ вынести мусор"))
    errors = []
    if not info:
        errors.append("после рестарта перенос не восстановился")
        return errors
    if info["message"] != "вынести мусор":
        errors.append(f"текст восстановлен неверно: {info['message']!r}")
    if info["chat_id"] != 42:
        errors.append("чат восстановлен неверно")
    return errors


def case_snooze_empty_message():
    if bot.recover_snooze_info(FakeMessage("")) is not None:
        return ["из пустого сообщения не должно восстанавливаться напоминание"]
    return []


def case_cron_job_scheduled():
    sched.add_cron_job(None, 11, 42, "зарядка", "mon-fri", "09:00", MSK, None)
    job = next((j for j in sched.scheduler.get_jobs() if j.id == sched._job_id_recurring(11)), None)
    if job is None:
        return ["задача по дням недели не попала в планировщик"]
    fire = job.trigger.get_next_fire_time(None, datetime.now(ZoneInfo(MSK)))
    errors = []
    if fire is None:
        errors.append("у задачи нет следующего запуска")
    else:
        if fire.weekday() > 4:
            errors.append(f"первый запуск выпал на выходной: {fire:%A}")
        if (fire.hour, fire.minute) != (9, 0):
            errors.append(f"время запуска {fire.hour:02d}:{fire.minute:02d}, ожидалось 09:00")
    sched.remove_job(11, "recurring")
    return errors


def case_cron_respects_until():
    until = time.time() + 3 * 86400
    sched.add_cron_job(None, 12, 42, "зарядка", "mon-fri", "09:00", MSK, until)
    job = next((j for j in sched.scheduler.get_jobs() if j.id == sched._job_id_recurring(12)), None)
    if job is None:
        return ["задача не попала в планировщик"]
    end = job.trigger.end_date
    sched.remove_job(12, "recurring")
    if end is None:
        return ["срок окончания не передан в планировщик"]
    if abs(end.timestamp() - until) > 1:
        return [f"срок окончания сдвинут: {end}"]
    return []


def case_interval_respects_until():
    until = time.time() + 86400
    sched.add_recurring_job(None, 13, 42, "пить воду", 3600,
                            start_date=datetime.now(ZoneInfo(MSK)), until=until)
    job = next((j for j in sched.scheduler.get_jobs() if j.id == sched._job_id_recurring(13)), None)
    if job is None:
        return ["задача не попала в планировщик"]
    end = job.trigger.end_date
    sched.remove_job(13, "recurring")
    if end is None or abs(end.timestamp() - until) > 1:
        return [f"срок окончания не применён: {end}"]
    return []


def case_grace_time_is_generous():
    grace = sched.scheduler._job_defaults.get("misfire_grace_time")
    if not grace or grace < 60:
        return [f"misfire_grace_time={grace} — напоминание потеряется при короткой задержке"]
    return []


ADMIN_ID = 834815805
OVERVIEW = {
    "users": 12, "banned": 1, "new_week": 3, "new_day": 1, "reminders": 40,
    "once": 25, "recurring": 10, "by_days": 5, "notes": 7,
    "created_total": 130, "created_today": 4, "active_week": 6, "active_today": 2,
}
USER_ROWS = [
    {"user_id": 111, "username": "temoseee", "first_name": "Артём", "reminders_created": 90,
     "registered_at": time.time() - 30 * 86400, "is_banned": 0, "active": 12,
     "last_day": date(2026, 8, 8)},
    {"user_id": 222, "username": None, "first_name": "Гость <script>", "reminders_created": 3,
     "registered_at": time.time() - 86400, "is_banned": 1, "active": 0, "last_day": None},
]


class AdminMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class AdminUpdate:
    def __init__(self, user_id):
        self.message = AdminMessage()
        self.effective_user = type("U", (), {"id": user_id})()


class AdminContext:
    def __init__(self, args):
        self.args = args


def run_admin(user_id, args):
    upd = AdminUpdate(user_id)
    saved = (db.get_admin_overview, db.get_user_activity)
    db.get_admin_overview = lambda: dict(OVERVIEW)
    db.get_user_activity = lambda limit=20: USER_ROWS[:limit]
    try:
        asyncio.run(admin.cmd_admin(upd, AdminContext(args)))
    finally:
        db.get_admin_overview, db.get_user_activity = saved
    return upd.message.replies


def case_admin_only():
    errors = []
    if run_admin(ADMIN_ID + 1, []):
        errors.append("посторонний получил ответ от админки")
    if run_admin(0, ["users"]):
        errors.append("админка ответила пользователю без прав")
    if run_admin(ADMIN_ID + 1, ["ban", "111"]):
        errors.append("посторонний смог дойти до бана")
    if not run_admin(ADMIN_ID, []):
        errors.append("админ не получил сводку")
    return errors


def case_several_admins():
    import config
    saved = config.ADMIN_IDS
    config.ADMIN_IDS = config._parse_ids("834815805, 555, 0, мусор")
    try:
        errors = []
        if config.ADMIN_IDS != {834815805, 555}:
            errors.append(f"список админов разобран неверно: {config.ADMIN_IDS}")
        if not run_admin(555, []):
            errors.append("второй админ из списка не получил доступ")
        if run_admin(556, []):
            errors.append("чужой id прошёл проверку")
    finally:
        config.ADMIN_IDS = saved
    return errors


def case_admin_overview_numbers():
    text = admin.format_overview(OVERVIEW, jobs=57)
    errors = []
    for fragment in ("Всего: 12", "Активны за неделю: 6", "Сейчас активных: 40",
                     "Разовых: 25", "По дням недели: 5", "Сегодня: 4", "57"):
        if fragment not in text:
            errors.append(f"нет в сводке: {fragment!r}")
    if "3.3" not in text:
        errors.append("не посчитано среднее число напоминаний на человека")
    if "50%" not in text:
        errors.append("не посчитана доля активных")
    return errors


def case_admin_users_list():
    text = admin.format_users(USER_ROWS)
    errors = []
    if "@temoseee" not in text:
        errors.append("нет юзернейма")
    if "90 / 12" not in text:
        errors.append("нет счётчиков создано/активно")
    if "🚫" not in text:
        errors.append("бан не отмечен")
    if "<script>" in text:
        errors.append("имя пользователя не экранировано")
    return errors


def case_admin_never_shows_text():
    # смысл шифрования пропадёт, если админка начнёт показывать содержимое
    text = admin.format_overview(OVERVIEW, jobs=1) + admin.format_users(USER_ROWS)
    src = open("admin.py", encoding="utf-8").read()
    errors = []
    for leak in ("'message'", '"message"', "get_reminders", "get_notes"):
        if leak in src:
            errors.append(f"админка обращается к содержимому: {leak}")
    if "вынести мусор" in text:
        errors.append("в выводе оказался текст напоминания")
    return errors



def case_long_list_fits_telegram():
    rows = [{"id": i, "type": "once", "message": "щ" * 200, "next_fire": time.time() + 600,
             "interval_seconds": None} for i in range(1, 51)]
    text, markup = bot.build_reminders_message(rows, 1, "ru", MSK)
    buttons = sum(len(r) for r in markup.inline_keyboard)
    errors = []
    if len(text) > 4096:
        errors.append(f"сообщение из {len(text)} символов телеграм отклонит")
    if buttons > 100:
        errors.append(f"{buttons} кнопок — больше предела телеграма")
    if "1." not in text or "8." not in text:
        errors.append("на странице должно быть восемь напоминаний")
    return errors


def case_long_notes_fit_telegram():
    notes = [{"id": i, "text": "щ" * 500} for i in range(1, 60)]
    text, markup = bot.build_notes_message(notes, "ru")
    errors = []
    if len(text) > 4096:
        errors.append(f"сообщение из {len(text)} символов телеграм отклонит")
    if sum(len(r) for r in markup.inline_keyboard) > 100:
        errors.append("кнопок больше предела")
    return errors


def case_pagination_navigation():
    rows = [{"id": i, "type": "once", "message": f"дело {i}", "next_fire": time.time() + 600,
             "interval_seconds": None} for i in range(1, 30)]
    _, first = bot.build_reminders_message(rows, 1, "ru", MSK, page=0)
    text2, second = bot.build_reminders_message(rows, 1, "ru", MSK, page=1)
    cb1, cb2 = all_callbacks(first), all_callbacks(second)
    errors = []
    if "rpage_1" not in cb1:
        errors.append("с первой страницы нельзя уйти вперёд")
    if "rpage_0" not in cb2:
        errors.append("со второй страницы нельзя вернуться")
    if "rpage_-1" in cb1:
        errors.append("предложен переход перед первой страницей")
    if "9. " not in text2:
        errors.append("вторая страница начинается не с девятого")
    out_text, _ = bot.build_reminders_message(rows, 1, "ru", MSK, page=999)
    if "дело 29" not in out_text:
        errors.append("запрос несуществующей страницы не прижался к последней")
    return errors


def case_double_tap_guarded():
    bot._HANDLED_TAPS.clear()
    first = bot.already_handled(1, 2, "snooze_5_60")
    second = bot.already_handled(1, 2, "snooze_5_60")
    other = bot.already_handled(1, 3, "snooze_5_60")
    errors = []
    if first:
        errors.append("первое нажатие посчиталось повтором")
    if not second:
        errors.append("повторное нажатие той же кнопки не отсекается")
    if other:
        errors.append("нажатие в другом сообщении принято за повтор")
    return errors


def case_message_fits_guard():
    huge = ["ю" * 5000, "хвост"]
    out = bot._fit(huge)
    if len(out) > 4096:
        return [f"страховка не сработала: {len(out)} символов"]
    return []


def case_year_shown_only_when_needed():
    from datetime import datetime as dt
    tz = ZoneInfo(MSK)
    now = dt.now(tz)
    same = bot._fmt_datetime(now.replace(month=12, day=1), "ru")
    other = bot._fmt_datetime(now.replace(year=now.year + 1, month=1, day=5), "ru")
    errors = []
    if len(same.split(".")) > 2:
        errors.append(f"в этом году год лишний: {same}")
    if len(other.split(".")) < 3:
        errors.append(f"для другого года год не показан: {other}")
    return errors


cases = [
    ("заметки экранируются", case_notes_escaped),
    ("длинный список влезает в телеграм", case_long_list_fits_telegram),
    ("длинные заметки влезают в телеграм", case_long_notes_fit_telegram),
    ("листание страниц", case_pagination_navigation),
    ("двойное нажатие не срабатывает дважды", case_double_tap_guarded),
    ("страховка по длине сообщения", case_message_fits_guard),
    ("год показывается только когда нужен", case_year_shown_only_when_needed),
    ("админка только для владельца", case_admin_only),
    ("несколько админов через запятую", case_several_admins),
    ("сводка считает верно", case_admin_overview_numbers),
    ("список пользователей", case_admin_users_list),
    ("админка не показывает тексты", case_admin_never_shows_text),
    ("интервалы читаются по-русски", case_interval_wording),
    ("текст в списке не обрезается", case_list_text_not_truncated),
    ("разовое напоминание можно перенести", case_once_row_movable),
    ("напоминание по дням недели переносить нельзя", case_weekday_row_not_movable),
    ("часовое время отличается от длительности", case_wall_clock_detected),
    ("время на часах сохраняется при смене города", case_wall_clock_keeps_the_hour),
    ("длительность не двигается при смене города", case_duration_keeps_the_moment),
    ("напоминание по часам едет за городом", case_wall_clock_reminder_moves_with_city),
    ("меню переноса со всеми вариантами", case_move_menu_offsets),
    ("перенос работает после рестарта", case_snooze_recovered_after_restart),
    ("пустое сообщение не восстанавливается", case_snooze_empty_message),
    ("задача по будням встаёт на будний день", case_cron_job_scheduled),
    ("срок окончания у задачи по дням недели", case_cron_respects_until),
    ("срок окончания у интервальной задачи", case_interval_respects_until),
    ("запас на опоздание достаточный", case_grace_time_is_generous),
]

ok = fail = 0
for name, case in cases:
    errs = case()
    if errs:
        fail += 1
        print(f"[FAIL] {name}")
        for e in errs:
            print(f"       {e}")
    else:
        ok += 1
        print(f"[OK]   {name}")

print(f"\nИтого: {ok} OK, {fail} FAIL")
if fail:
    exit(1)
