"""
Тесты сборки сообщений и планировщика. Запускать:
    python -X utf8 test_bot.py
"""
import os
import time

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("DATABASE_URL", "postgresql://dummy")

from datetime import datetime
from zoneinfo import ZoneInfo

import bot
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
    _, markup = bot.build_reminders_message([r], 1, "ru", MSK)
    cbs = all_callbacks(markup)
    errors = []
    if "move_7" not in cbs:
        errors.append(f"нет кнопки переноса: {cbs}")
    if "del_7" not in cbs:
        errors.append(f"нет кнопки удаления: {cbs}")
    return errors


def case_weekday_row_not_movable():
    r = {"id": 8, "type": "recurring", "message": "зарядка", "next_fire": None,
         "interval_seconds": None, "days_of_week": "mon-fri", "at_time": "09:00"}
    _, markup = bot.build_reminders_message([r], 1, "ru", MSK)
    cbs = all_callbacks(markup)
    labels = " ".join(all_labels(markup))
    errors = []
    if "move_8" in cbs:
        errors.append("предложен перенос напоминания по дням недели")
    if "del_8" not in cbs:
        errors.append("нет кнопки удаления")
    if "будням" not in labels:
        errors.append(f"в подписи не видно дней недели: {labels}")
    if "09:00" not in labels:
        errors.append(f"в подписи не видно времени: {labels}")
    return errors


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


cases = [
    ("заметки экранируются", case_notes_escaped),
    ("разовое напоминание можно перенести", case_once_row_movable),
    ("напоминание по дням недели переносить нельзя", case_weekday_row_not_movable),
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
