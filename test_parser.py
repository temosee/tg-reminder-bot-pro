"""
Тесты парсера. Запускать после каждого значимого изменения:
    python -X utf8 test_parser.py
"""
import time
import parser as p

NOW = time.time()
DAY = 86400
HOUR = 3600
MINUTE = 60

def approx(ts, expected_delta, tolerance=5):
    """Проверяет что timestamp примерно соответствует NOW + delta."""
    return abs(ts - (NOW + expected_delta)) < tolerance

def check_error(text, substr=""):
    r = p.parse(text)
    if not r.get("error"):
        return [f"expected error, got type={r.get('type')!r} msg={r.get('message')!r}"]
    if substr and substr.lower() not in r["error"].lower():
        return [f"error doesn't mention {substr!r}: {r['error'][:80]}"]
    return []

def check(text, exp_type, exp_msg, exp_interval=None, exp_delta=None):
    r = p.parse(text)
    errors = []
    if r.get("error"):
        errors.append(f"got error: {r['error'][:80]}")
    if r.get("type") != exp_type:
        errors.append(f"type: want={exp_type!r} got={r.get('type')!r}")
    if r.get("message") != exp_msg:
        errors.append(f"msg: want={exp_msg!r} got={r.get('message')!r}")
    if exp_interval is not None and r.get("interval_seconds") != exp_interval:
        errors.append(f"interval: want={exp_interval} got={r.get('interval_seconds')}")
    if exp_delta is not None and r.get("next_fire") is not None:
        if not approx(r["next_fire"], exp_delta):
            errors.append(f"next_fire delta: want~{exp_delta}s got~{r['next_fire']-NOW:.0f}s")
    return errors

tests = [

    ("напомни через 30 минут попить воды",          "once", "попить воды",     None, 30*MINUTE),
    ("напомни через час пойти поесть",              "once", "пойти поесть",    None, HOUR),
    ("напомни через 2 часа вынести мусор",          "once", "вынести мусор",   None, 2*HOUR),
    ("напомни в 15:00 позвонить маме",              "once", "позвонить маме",  None, None),
    ("потом напомни через час попить воды",         "once", "попить воды",     None, HOUR),
    ("напомни через полтора часа сохранить файл",   "once", "сохранить файл",  None, int(1.5*HOUR)),

    ("напомни завтра купить продукты",              "once", "купить продукты", None, DAY),
    ("напомни послезавтра позвонить врачу",         "once", "позвонить врачу", None, 2*DAY),
    ("напомни через 2 дня сдать отчёт",             "once", "сдать отчёт",     None, 2*DAY),
    ("напомни через неделю продлить подписку",      "once", "продлить подписку", None, 7*DAY),

    ("напоминай вставать каждый час",               "recurring", "вставать",           3600, None),
    ("напоминай пить воду каждые 30 минут",         "recurring", "пить воду",           1800, None),
    ("напоминай каждые полтора часа сохранить проект","recurring","сохранить проект",   5400, None),
    ("напоминай делать разминку каждые 2 часа",     "recurring", "делать разминку",     7200, None),
    ("напомни пить воду каждые 30 минут",           "recurring", "пить воду",           1800, None),
    ("напомни мне пить воду каждые два часа",       "recurring", "пить воду",           7200, None),
    ("ставь напоминание каждые 20 минут размяться", "recurring", "размяться",           1200, None),

    ("кстати напомни через 45 минут выйти",         "once", "выйти",          None, 45*MINUTE),
    ("хочу напоминание через 45 минут выйти",       "once", "выйти",          None, 45*MINUTE),

    ("напомни мне через минут 30 налить воды",      "once", "налить воды",    None, 30*MINUTE),
    ("напомни через часов 2 поесть",                "once", "поесть",         None, 2*HOUR),

    ("прив напомни посмотреть направо через минуту","once", "посмотреть направо", None, MINUTE),

    ("напомни завтра в 14 часов сделать лабу",      "once", "сделать лабу",   None, None),
    ("напомни послезавтра в 9 часов купить билеты", "once", "купить билеты",  None, None),

    ("напомни завтра утром позвонить маме",          "once", "позвонить маме",       None, None),
    ("напомни завтра вечером вынести мусор",         "once", "вынести мусор",        None, None),
    ("напомни завтра днём сходить в банк",           "once", "сходить в банк",       None, None),
    ("напомни послезавтра утром зарядить телефон",   "once", "зарядить телефон",     None, None),

    ("напомни в 9 утра позвонить в банк",            "once", "позвонить в банк",     None, None),
    ("напомни в 10 вечера принять таблетку",         "once", "принять таблетку",     None, None),
    ("напомни в 3 дня встреча с клиентом",           "once", "встреча с клиентом",   None, None),

    ("напомни вечером проверить почту",              "once", "проверить почту",       None, None),
    ("напомни утром сделать зарядку",                "once", "сделать зарядку",       None, None),

    ("напомни через полчаса покушать",               "once", "покушать",              None, 30*MINUTE),
    ("напомни через пару часов отдохнуть",           "once", "отдохнуть",             None, 2*HOUR),
    ("напомни через пару минут",                     "once", "напоминание",            None, 2*MINUTE),

    ("минут через 20 напомни выпить воду",           "once", "выпить воду",           None, 20*MINUTE),

    ("напомни в пятницу позвонить",                  "once", "позвонить",             None, None),
    ("напомни в воскресенье встреча",                "once", "встреча",               None, None),
    ("напомни в субботу утром сделать уборку",       "once", "сделать уборку",        None, None),

    ("напомни сегодня вечером проверить",            "once", "проверить",             None, None),

    ("напомни в обед поесть",                        "once", "поесть",                None, None),

    ("напомни завтра с утра позвонить",              "once", "позвонить",             None, None),

    ("не забудь напомнить купить хлеб через час",    "once", "купить хлеб",           None, HOUR),
    ("хочу чтобы ты напомнил в 19:00 поужинать",    "once", "поужинать",             None, None),

    ("напомни вечерком проверить почту",             "once", "проверить почту",       None, None),

    ("привет друг напомни мне завтра о лабе в 13 часов дня", "once", "о лабе",  None, None),
    ("напомни в 9 часов утра сделать зарядку",       "once", "сделать зарядку", None, None),

    ("поставь будильник на 7 утра",                  "once", "напоминание",      None, None),
    ("хочу напоминание на 23:00 лечь спать",         "once", "лечь спать",       None, None),
    ("мне нужно напоминание завтра в обед поесть",   "once", "поесть",           None, None),
    ("дай знать через час что надо идти",            "once", "надо идти",        None, HOUR),

    ("напомни сегодня днем что нужно поменять постель", "once", "нужно поменять постель", None, None),

    ("напомни послезавтра ближе к вечеру покакать",  "once", "покакать",         None, None),

    ("напомни за 15 минут до начала пары в 10:00 похавать", "once", "похавать",  None, None),
    ("напомни за 30 минут до встречи в 15:00 подготовиться", "once", "подготовиться", None, None),

    ("напомни в следующую субботу утром сходить в зал", "once", "сходить в зал", None, None),
    ("напомни в следующий понедельник сдать курсовую",  "once", "сдать курсовую", None, None),

    ("напомни завтра в 21 час о том, что через 3 дня будет пара по пгс",
     "once", "о том, что через 3 дня будет пара по пгс", None, None),
    ("напомни сегодня вечером о том, что завтра утром пара по пгс",
     "once", "о том, что завтра утром пара по пгс", None, None),

    ("напомни через час выпить таблетку",            "once", "выпить таблетку",  None, HOUR),

    ("напоминай мне каждые два дня в 20 часов делать разминку",  "recurring", "делать разминку", 2*DAY, None),
    ("напоминай каждый день в 8 утра делать зарядку",            "recurring", "делать зарядку",  DAY,   None),
    ("напоминай пить воду каждые 2 часа",                        "recurring", "пить воду",       2*HOUR, None),
    ("ставь напоминание раз в 3 дня поливать цветы",             "recurring", "поливать цветы",  3*DAY,  None),

    ("remind me in 30 minutes to drink water",        "once", "drink water",        None, 30*MINUTE),
    ("remind me in 2 hours to take a pill",           "once", "take a pill",        None, 2*HOUR),
    ("remind me in 3 hours to drink milk",            "once", "drink milk",         None, 3*HOUR),
    ("remind me in 1 day to call mom",                "once", "call mom",           None, DAY),
    ("remind me in 45 minutes to go outside",         "once", "go outside",         None, 45*MINUTE),
    ("remind me in 10 minutes",                       "once", "reminder",           None, 10*MINUTE),
    ("let me know in 15 minutes",                     "once", "reminder",           None, 15*MINUTE),
    ("don't forget to call John in 2 hours",          "once", "call John",          None, 2*HOUR),
    ("remind me in a couple hours to check mail",     "once", "check mail",         None, 2*HOUR),

    ("remind me tomorrow to buy groceries",           "once", "buy groceries",      None, DAY),
    ("remind me tomorrow at 9am to go to gym",        "once", "go to gym",          None, None),
    ("remind me tomorrow morning to call doctor",     "once", "call doctor",        None, None),
    ("remind me tomorrow evening to take medicine",   "once", "take medicine",      None, None),
    ("set a reminder for tomorrow morning to run",    "once", "run",                None, None),
    ("remind me today in the evening to check",       "once", "check",              None, None),

    ("remind me on Friday to submit report",          "once", "submit report",      None, None),
    ("remind me on Monday to buy tickets",            "once", "buy tickets",        None, None),
    ("remind me next Saturday to clean the house",    "once", "clean the house",    None, None),
    ("remind me next Monday morning to call bank",    "once", "call bank",          None, None),

    ("remind me at 15:00 to call mom",                "once", "call mom",           None, None),
    ("remind me at 9am to do workout",                "once", "do workout",         None, None),
    ("remind me at 10pm to take medicine",            "once", "take medicine",      None, None),
    ("set a reminder at 8:30 to wake up",             "once", "wake up",            None, None),
    ("remind me at noon to eat lunch",                "once", "eat lunch",          None, None),

    ("remind me in the morning to drink water",       "once", "drink water",        None, None),
    ("remind me in the evening to check email",       "once", "check email",        None, None),
    ("remind me tonight to take pills",               "once", "take pills",         None, None),

    ("remind me every 2 hours to drink water",        "recurring", "drink water",   2*HOUR,    None),
    ("remind me every 30 minutes to stretch",         "recurring", "stretch",       30*MINUTE, None),
    ("remind me every day to exercise",               "recurring", "exercise",      DAY,       None),
    ("remind me every week to call parents",          "recurring", "call parents",  7*DAY,     None),
    ("every hour check email",                        "recurring", "check email",   HOUR,      None),
    ("every 20 minutes drink water",                  "recurring", "drink water",   20*MINUTE, None),
    ("every 3 hours take medicine",                   "recurring", "take medicine", 3*HOUR,    None),
    ("remind me every day at 8am to do workout",      "recurring", "do workout",    DAY,       None),

    ("remind me to pick up pastries at 7:00 pm today","once", "pick up pastries", None, None),
    ("remind me at 7pm to call",                      "once", "call",             None, None),
    ("remind me at 9:30 am to wake up",               "once", "wake up",          None, None),
    ("remind me tomorrow at 8:00 pm to go out",       "once", "go out",           None, None),
    ("remind me tomorrow at 15 to submit",            "once", "submit",           None, None),
    ("remind me to go to the shop at 12",             "once", "go to the shop",   None, None),
    ("remind me at 9 to call",                        "once", "call",             None, None),

    ("remind me today at 7pm that tomorrow at 7am will be exam",
                                                       "once", "tomorrow at 7am will be exam", None, None),
    ("remind me at 8pm that in 3 days there is a meeting",
                                                       "once", "in 3 days there is a meeting", None, None),
    ("remind me on Friday that next Monday is deadline",
                                                       "once", "next Monday is deadline",       None, None),
    ("remind me tomorrow morning that today I have gym",
                                                       "once", "today I have gym",              None, None),

    ("напомни сегодня в 19:00 что завтра в 7 утра экзамен",
                                                       "once", "завтра в 7 утра экзамен",       None, None),
    ("напомни в пятницу что в понедельник дедлайн",
                                                       "once", "в понедельник дедлайн",         None, None),
    ("напомни завтра в 10:00 что через 3 дня сдача проекта",
                                                       "once", "через 3 дня сдача проекта",     None, None),

    ("можешь напомнить мне в субботу утром сходить в магазин",
                                                       "once", "сходить в магазин",             None, None),
    ("было бы круто если бы ты напомнил завтра вечером позвонить другу",
                                                       "once", "позвонить другу",               None, None),
    ("напомни мне пожалуйста через 2 часа выпить лекарство",
                                                       "once", "выпить лекарство",              None, 2*HOUR),

    ("через час скажи мне позвонить другу",            "once", "позвонить другу",               None, HOUR),
    ("скажи мне через 30 минут поесть",                "once", "поесть",                        None, 30*MINUTE),

    ("напомни через двадцать пять минут выпить воду",  "once", "выпить воду",                   None, 25*MINUTE),
    ("напомни через тридцать минут позвонить",         "once", "позвонить",                     None, 30*MINUTE),
    ("напоминай каждые двадцать минут пить воду",      "recurring", "пить воду",                20*MINUTE, None),

    ("напомни через час #работа сдать отчёт",          "once", "сдать отчёт",                   None, HOUR),
    ("напоминай каждый день в 9 #спорт делать зарядку","recurring", "делать зарядку",           DAY, None),
    ("напомни завтра #семья позвонить маме",           "once", "позвонить маме",                None, DAY),

    ("напомни через 4 дня в 15 часов поделать скрины", "once", "поделать скрины",               None, None),
    ("напомни через 2 дня в 10 часов встреча",         "once", "встреча",                       None, None),
    ("напомни через 3 дня в 20 часов позвонить",       "once", "позвонить",                     None, None),

    ("напоминай каждые 2 дня с 9 утра делать зарядку", "recurring", "делать зарядку",           2*DAY, None),
    ("напомни завтра с 10 часов начать работу",        "once", "начать работу",                 None, None),

    ("напоминай каждый понедельник в 10:00 сдавать отчёт","recurring","сдавать отчёт",          7*DAY, None),
    ("напоминай каждую пятницу вечером проверить задачи", "recurring","проверить задачи",       7*DAY, None),
    ("напоминай каждое воскресенье в 21 час посмотреть сериал","recurring","посмотреть сериал", 7*DAY, None),
    ("напоминай каждую среду в 9 утра созвон с командой",  "recurring","созвон с командой",     7*DAY, None),
    ("каждый вторник в 15 часов напомни позвонить врачу",  "recurring","позвонить врачу",       7*DAY, None),

    ("remind me every monday at 10:00 to submit report",   "recurring","submit report",          7*DAY, None),
    ("remind me every friday at 6pm to check tasks",       "recurring","check tasks",            7*DAY, None),
    ("every tuesday at 3pm call the team",                 "recurring","call the team",          7*DAY, None),
    ("every sunday at 9am do workout",                     "recurring","do workout",             7*DAY, None),

    ("напомни через час и позвонить маме",              "once", "позвонить маме",                None, HOUR),
    ("напомни завтра а поговорить с другом",            "once", "поговорить с другом",           None, DAY),

    ("напоминай пить воду каждые 2 часа начиная с утра","recurring", "пить воду",               2*HOUR, None),

    ("напомни ночью принять таблетку",                  "once", "принять таблетку",              None, None),

    ("напомни через минуту позвонить",                  "once", "позвонить",                     None, MINUTE),

    ("напомни через 30 минут",                          "once", "напоминание",                   None, 30*MINUTE),
    ("remind me in 1 hour",                             "once", "reminder",                      None, HOUR),

    ("ставь напоминание раз в час проверить почту",     "recurring", "проверить почту",          HOUR, None),
    ("раз в 2 часа напомни выпить воды",                "recurring", "выпить воды",              2*HOUR, None),

    ("напомни в полдень пообедать",                     "once", "пообедать",                     None, None),

    ("напомни послезавтра в 14:00 встреча",             "once", "встреча",                       None, None),

    ("дай знать через 2 часа что надо уходить",        "once", "надо уходить",                  None, 2*HOUR),

    ("напомни   через   час   поесть",                  "once", "поесть",                        None, HOUR),
    ("пожалуйста напомни мне пожалуйста через час пойти в магазин", "once", "пойти в магазин",  None, HOUR),

    ("remind me every 2 days at 8am to workout",        "recurring", "workout",                  2*DAY, None),
    ("remind me every 3 days to call parents",          "recurring", "call parents",              3*DAY, None),

    ("remind me tomorrow in the morning to run",        "once", "run",                           None, None),
    ("remind me in the morning to drink water",         "once", "drink water",                   None, None),
]

error_tests = [
    # bare number ≤12 without am/pm in RECURRING → must ask for clarification
    ("remind every day at 8 to stretch",  "AM or PM"),
    ("remind me every week at 9 to call", "AM or PM"),
    # invalid hour
    ("напомни в 25 часов выйти",          ""),
    ("напомни в 99:00 позвонить",         ""),
]

ok = fail = 0
for text, exp_type, exp_msg, exp_interval, exp_delta in tests:
    errs = check(text, exp_type, exp_msg, exp_interval, exp_delta)
    if errs:
        fail += 1
        print(f"[FAIL] {text!r}")
        for e in errs:
            print(f"       {e}")
    else:
        ok += 1
        print(f"[OK]   {text!r}")

for text, substr in error_tests:
    errs = check_error(text, substr)
    if errs:
        fail += 1
        print(f"[FAIL] {text!r}")
        for e in errs:
            print(f"       {e}")
    else:
        ok += 1
        print(f"[OK]   {text!r}")

print(f"\nИтого: {ok} OK, {fail} FAIL")
if fail:
    exit(1)
