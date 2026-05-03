import re
import time
import dateparser
import zoneinfo
from datetime import datetime, timedelta
import config

_TZ = zoneinfo.ZoneInfo(config.TIMEZONE)

def _get_tz(tz_name: str | None) -> zoneinfo.ZoneInfo:
    if tz_name:
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            pass
    return _TZ

WORD_NUMBERS = {
    "одну": 1, "одной": 1, "один": 1, "одного": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2, "пару": 2, "пара": 2,
    "три": 3, "трёх": 3, "трех": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10, "пятнадцать": 15,
    "двадцать": 20, "тридцать": 30, "сорок": 40,
    "полтора": 1.5, "полторы": 1.5,
    "1.5": 1.5, "1,5": 1.5,
}

UNITS_TO_SECONDS = {
    "секунду": 1, "секунды": 1, "секунд": 1, "сек": 1,
    "минуту": 60, "минуты": 60, "минут": 60, "минуток": 60, "мин": 60,
    "час": 3600, "часа": 3600, "часов": 3600, "часок": 3600, "часика": 3600, "часиков": 3600, "ч": 3600,
    "день": 86400, "дня": 86400, "дней": 86400, "дне": 86400, "дни": 86400,
    "неделю": 604800, "недели": 604800, "недель": 604800, "неделя": 604800, "неделе": 604800,
    "месяц": 2592000, "месяца": 2592000, "месяцев": 2592000, "месяце": 2592000,
}

# Часы по умолчанию для частей дня
TIME_OF_DAY = {
    "утром": 8, "утра": 8, "с утра": 8,
    "днём": 13, "дня": 13, "в обед": 13, "обед": 13, "обеда": 13,
    "вечером": 17, "вечера": 17, "вечерком": 17, "ближе к вечеру": 18,
    "ночью": 22, "ночи": 22,
    "в полночь": 0, "полночь": 0, "полуночи": 0,
    "в полдень": 12, "полдень": 12, "полудня": 12,
}

# Дни недели (рус. → weekday 0=пн)
WEEKDAYS_RU = {
    "понедельник": 0, "понедельника": 0,
    "вторник": 1, "вторника": 1,
    "среду": 2, "среды": 2,
    "четверг": 3, "четверга": 3,
    "пятницу": 4, "пятницы": 4,
    "субботу": 5, "субботы": 5,
    "воскресенье": 6, "воскресенья": 6,
}

_ALL_UNITS = (
    r"секунд[у-ы]?|сек|минут[у-ыок]*|мин|часи?[кова]*|час[а-ов]*"
    r"|дн[яейи]|день|дней|недел[юьие]|недель|месяц[а-ев]?"
)

_WEEKDAY_PAT = "|".join(WEEKDAYS_RU.keys())

def _parse_number(s: str) -> float | None:
    s = s.strip().lower()
    if s in WORD_NUMBERS:
        return WORD_NUMBERS[s]
    try:
        return float(s)
    except ValueError:
        return None

def _normalize(text: str) -> str:
    """Нормализует разговорные синонимы перед разбором."""
    subs = [
        (r"\bминуток\b", "минут"),
        (r"\bминутк[уи]\b", "минуту"),
        (r"\bчасок\b", "час"),
        (r"\bчасика?\b", "час"),
        (r"\bденёк\b", "день"),
        (r"\bдень?ка\b", "дня"),
        (r"\bнедельк[уи]\b", "неделю"),
        (r"\bсекундочк[уи]\b", "секунду"),
        (r"\bвечерком\b", "вечером"),
        (r"\bутречком\b", "утром"),
        (r"\bночкой\b",   "ночью"),
        (r"\bполчас[аик]*\b", "30 минут"),
        (r"\bпол\s+час[аик]*\b", "30 минут"),
        (r"\bбудильник\b", "напоминание"),      # будильник → напоминание (trigger + normalize)
        (r"\bна\s+(\d{1,2}:\d{2})\b", r"в \1"),                              # на 23:00 → в 23:00
        (r"\bна\s+(\d{1,2})\s+(час)", r"в \1 \2"),                          # на 7 часов → в 7 часов
        (r"\bна\s+(\d{1,2})\s+(утра|дня|вечера|ночи)\b", r"в \1 \2"),       # на 7 утра → в 7 утра
        (r"\bна\s+(?=завтра\b|сегодня\b|послезавтра\b)", ""),               # на завтра → завтра
        (r"\bчасо\b", "часов"),
        (r"\bминутк?\b", "минут"),
    ]
    for pat, repl in subs:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    return text

def _next_weekday(target_wd: int, tz: zoneinfo.ZoneInfo = None) -> datetime:
    """Возвращает дату следующего вхождения дня недели (не сегодня)."""
    if tz is None:
        tz = _TZ
    now = datetime.now(tz=tz)
    days_ahead = target_wd - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return now + timedelta(days=days_ahead)

def _resolve_tod(text: str) -> int | None:
    """Возвращает час по умолчанию для части дня из текста, или None."""
    for phrase, h in TIME_OF_DAY.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", text):
            return h
    return None

def _resolve_time(base_day: datetime, text: str) -> float | None:
    """Возвращает timestamp для base_day с учётом времени из текста."""
    m_hm = re.search(r"в\s+(\d{1,2}):(\d{2})", text)
    m_h  = re.search(r"в\s+(\d{1,2})\s+час(?:ов|а)?\b", text)
    m_tod_d = re.search(r"в\s+(\d{1,2})(?::(\d{2}))?\s*(утра|дня|вечера|ночи)\b", text)

    if m_hm:
        h, mn = int(m_hm.group(1)), int(m_hm.group(2))
        return base_day.replace(hour=h, minute=mn, second=0, microsecond=0).timestamp()
    if m_tod_d:
        h_raw = int(m_tod_d.group(1))
        mn = int(m_tod_d.group(2) or 0)
        tod = m_tod_d.group(3)
        if tod == "утра":
            h = h_raw % 12
        else:
            h = h_raw % 12 + 12
        return base_day.replace(hour=h, minute=mn, second=0, microsecond=0).timestamp()
    if m_h:
        h = int(m_h.group(1))
        return base_day.replace(hour=h, minute=0, second=0, microsecond=0).timestamp()
    h = _resolve_tod(text)
    if h is not None:
        return base_day.replace(hour=h, minute=0, second=0, microsecond=0).timestamp()
    return None

def _parse_interval(text: str, tz: zoneinfo.ZoneInfo = None) -> int | None:
    if tz is None:
        tz = _TZ
    text = text.lower().strip()
    text = _normalize(text)

    if re.search(r"каждый\s+час", text):
        return 3600
    if re.search(r"каждую\s+минуту", text):
        return 60
    if re.search(r"каждую\s+секунду", text):
        return 1
    if re.search(r"каждый\s+день|каждые\s+сутки", text):
        return 86400
    if re.search(r"каждую\s+неделю", text):
        return 604800

    # раз в N дней/недель/месяцев
    m_raz = re.search(
        r"раз\s+в\s+([\wа-яё]+)\s+(дн[яейи]|день|дней|сутки?|недел[юьи]|недель|месяц[а-ев]?)\b",
        text
    )
    if m_raz:
        num = _parse_number(m_raz.group(1))
        unit = m_raz.group(2)
        if num is not None:
            mult = UNITS_TO_SECONDS.get(unit, 86400)
            return int(num * mult)

    # каждые N дней/недель
    m_days = re.search(
        r"каждые?\s+([\wа-яё]+)\s+(дн[яейи]|день|дней|сутки?|недел[юьи]|недель)\b",
        text
    )
    if m_days:
        num = _parse_number(m_days.group(1))
        unit = m_days.group(2)
        if num is not None:
            mult = UNITS_TO_SECONDS.get(unit, 86400)
            return int(num * mult)

    # каждые N минут/часов
    m = re.search(
        r"каждые?\s+([\wа-яё.,]+(?:\s+[\wа-яё]+)?)\s+(секунд[у-ы]?|сек|минут[у-ы]?|мин|час[а-ов]*|ч)\b",
        text
    )
    if m:
        num = _parse_number(m.group(1))
        unit = m.group(2)
        if num is not None:
            mult = UNITS_TO_SECONDS.get(unit, 60)
            return int(num * mult)

    return None

def _parse_once_delta(text: str, tz: zoneinfo.ZoneInfo = None) -> float | None:
    """Разбирает относительное/именованное время → unix timestamp."""
    if tz is None:
        tz = _TZ
    text = _normalize(text.lower())

    if re.search(r"\bзавтра\b", text):
        tomorrow = datetime.now(tz=tz) + timedelta(days=1)
        ts = _resolve_time(tomorrow, text)
        return ts if ts is not None else time.time() + 86400

    if re.search(r"\bпослезавтра\b", text):
        day2 = datetime.now(tz=tz) + timedelta(days=2)
        ts = _resolve_time(day2, text)
        return ts if ts is not None else time.time() + 172800

    if re.search(r"\bсегодня\b", text):
        today = datetime.now(tz=tz)
        ts = _resolve_time(today, text)
        if ts is not None:
            if ts < time.time():
                ts += 86400  # время уже прошло — переносим на завтра
            return ts
        return None  # "сегодня" без времени → не распознаём как дельту

    _ORDINAL_TO_DAY = {
        "первого": 1, "второго": 2, "третьего": 3,
        "четвёртого": 4, "четвертого": 4, "пятого": 5, "шестого": 6,
        "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10,
        "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
        "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16,
        "семнадцатого": 17, "восемнадцатого": 18, "девятнадцатого": 19,
        "двадцатого": 20, "двадцать первого": 21, "двадцать второго": 22,
        "двадцать третьего": 23, "двадцать четвёртого": 24, "двадцать четвертого": 24,
        "двадцать пятого": 25, "двадцать шестого": 26, "двадцать седьмого": 27,
        "двадцать восьмого": 28, "двадцать девятого": 29,
        "тридцатого": 30, "тридцать первого": 31,
    }
    _day_num = None
    m_day_digit = re.search(r'\b(\d{1,2})(?:-?го|-?е|-?ое|-?ого)?\s+числ[а-яё]*\b', text)
    m_day_word = re.search(r'\b(' + '|'.join(sorted(_ORDINAL_TO_DAY, key=len, reverse=True)) + r')\s+числ[а-яё]*\b', text)
    if m_day_digit:
        _day_num = int(m_day_digit.group(1))
    elif m_day_word:
        _day_num = _ORDINAL_TO_DAY[m_day_word.group(1)]
    if _day_num and 1 <= _day_num <= 31:
        now = datetime.now(tz=tz)
        try:
            target = now.replace(day=_day_num, hour=9, minute=0, second=0, microsecond=0)
        except ValueError:
            target = None
        if target is None or target.timestamp() <= time.time():
            try:
                m, y = (now.month % 12 + 1, now.year + (1 if now.month == 12 else 0))
                target = now.replace(year=y, month=m, day=_day_num, hour=9, minute=0, second=0, microsecond=0)
            except ValueError:
                target = None
        if target:
            ts = _resolve_time(target, text)
            return ts if ts is not None else target.timestamp()

    m_next_wd = re.search(r"\bследующ(?:ую|ий|ее|его|ей)\s+(" + _WEEKDAY_PAT + r")\b", text)
    if m_next_wd:
        wd = WEEKDAYS_RU[m_next_wd.group(1)]
        now = datetime.now(tz=tz)
        days_ahead = (wd - now.weekday()) % 7
        if days_ahead < 7:
            days_ahead += 7  # "следующую" = всегда следующая неделя
        if days_ahead == 0:
            days_ahead = 7
        target_day = now + timedelta(days=days_ahead)
        ts = _resolve_time(target_day, text)
        if ts is not None:
            return ts
        return target_day.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()

    m_wd = re.search(r"\bв\s+(" + _WEEKDAY_PAT + r")\b", text)
    if m_wd:
        wd = WEEKDAYS_RU[m_wd.group(1)]
        target_day = _next_weekday(wd, tz)
        ts = _resolve_time(target_day, text)
        if ts is not None:
            return ts
        # нет времени — ставим 09:00
        return target_day.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()

    if re.search(r"через\s+неделю\b", text):
        return time.time() + 604800
    if re.search(r"через\s+день\b", text):
        return time.time() + 86400
    if re.search(r"через\s+час\b", text):
        return time.time() + 3600
    if re.search(r"через\s+минуту\b", text):
        return time.time() + 60
    if re.search(r"через\s+секунду\b", text):
        return time.time() + 1

    def _resolve_unit(unit: str, num: float) -> float | None:
        mult = UNITS_TO_SECONDS.get(unit)
        if mult is None:
            for key, val in UNITS_TO_SECONDS.items():
                if unit.startswith(key[:3]):
                    mult = val
                    break
        if mult:
            return time.time() + num * mult
        return None

    # "через N единиц" — обычный порядок
    m = re.search(rf"через\s+([\wа-яё.,]+(?:\s+[\wа-яё]+)?)\s+({_ALL_UNITS})\b", text)
    if m:
        num = _parse_number(m.group(1))
        if num is not None:
            ts = _resolve_unit(m.group(2), num)
            if ts:
                return ts

    # "через единиц N" — обратный порядок ("через минут 30")
    m_inv = re.search(rf"через\s+({_ALL_UNITS})\s+([\wа-яё\d]+)\b", text)
    if m_inv:
        num = _parse_number(m_inv.group(2))
        if num is not None:
            ts = _resolve_unit(m_inv.group(1), num)
            if ts:
                return ts

    # "единиц через N" — ещё один порядок ("минут через 20")
    m_inv2 = re.search(rf"({_ALL_UNITS})\s+через\s+([\wа-яё\d]+)\b", text)
    if m_inv2:
        num = _parse_number(m_inv2.group(2))
        if num is not None:
            ts = _resolve_unit(m_inv2.group(1), num)
            if ts:
                return ts

    m_before = re.search(rf"за\s+({_NUM_PAT})\s+({_ALL_UNITS})\s+до\b", text)
    if m_before:
        num = _parse_number(m_before.group(1))
        unit_str = m_before.group(2)
        if num is not None:
            mult = UNITS_TO_SECONDS.get(unit_str)
            if mult is None:
                for key, val in UNITS_TO_SECONDS.items():
                    if unit_str.startswith(key[:3]):
                        mult = val
                        break
            if mult:
                offset = int(num * mult)
                abs_ts = _parse_once_absolute(text, tz)
                if abs_ts:
                    return abs_ts - offset

    return None

def _parse_once_absolute(text: str, tz: zoneinfo.ZoneInfo = None) -> float | None:
    """Разбирает абсолютное время → unix timestamp."""
    if tz is None:
        tz = _TZ
    lower = _normalize(text.lower())

    def _today_or_tomorrow(h: int, mn: int = 0) -> float:
        now = datetime.now(tz=tz)
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target.timestamp() < time.time():
            target += timedelta(days=1)
        return target.timestamp()

    # "в обед"
    if re.search(r"\bв\s+обед\b|\bобед\b", lower):
        return _today_or_tomorrow(13)

    # "с утра" / "утром" / "вечером" etc. без числа
    h_tod = _resolve_tod(lower)
    if h_tod is not None and not re.search(r"в\s+\d", lower):
        return _today_or_tomorrow(h_tod)

    # "в N часов/часа/час"
    m_h = re.search(r"в\s+(\d{1,2})\s+час(?:ов|а)?\b", lower)
    if m_h:
        return _today_or_tomorrow(int(m_h.group(1)))

    # "в N утра/дня/вечера/ночи"
    m_tod = re.search(r"в\s+(\d{1,2})(?::(\d{2}))?\s*(утра|дня|вечера|ночи)\b", lower)
    if m_tod:
        h_raw, mn_raw, tod = int(m_tod.group(1)), int(m_tod.group(2) or 0), m_tod.group(3)
        h = h_raw % 12 if tod in ("утра", "ночи") else h_raw % 12 + 12
        return _today_or_tomorrow(h, mn_raw)

    # "в N" или "в N:MM" — всегда трактуем как время, НЕ передаём в dateparser
    m = re.search(r"\bв\s+(\d{1,2})(?::(\d{2}))?\b", lower)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        if 0 <= h <= 23:
            return _today_or_tomorrow(h, mn)

    # Fallback: dateparser на весь текст
    parsed = dateparser.parse(
        text,
        languages=["ru"],
        settings={"PREFER_DATES_FROM": "future", "TIMEZONE": config.TIMEZONE, "RETURN_AS_TIMEZONE_AWARE": True}
    )
    if parsed and parsed.timestamp() > time.time() - 60:
        return parsed.timestamp()

    return None

# Паттерн для удаления временны́х выражений из текста напоминания
_TOD_PAT = r"утром|утра|с\s+утра|днём|в\s+обед|обед[а]?|вечером|вечера|вечерком|ближе\s+к\s+вечеру|ночью|ночи|в\s+полночь|полночь|полуночи|в\s+полдень|полдень|полудня"
_WEEKDAY_STRIP = (
    r"(?:в\s+)?следующ(?:ую|ий|ее|его|ей)\s+(?:" + _WEEKDAY_PAT + r")"
    r"|в\s+(?:" + _WEEKDAY_PAT + r")"
)

# Числа (цифровые или словесные) для паттернов "обратного порядка"
_WORD_NUMS = "|".join(re.escape(k) for k in WORD_NUMBERS.keys())
_NUM_PAT = rf"(?:\d+(?:\.\d+)?|{_WORD_NUMS})"

_DELTA_STRIP = (
    rf"через\s+[\wа-яё.,]+(?:\s+[\wа-яё]+)?\s+(?:{_ALL_UNITS})\b"  # через N единиц
    rf"|через\s+(?:{_ALL_UNITS})\s+{_NUM_PAT}\b"                   # через единиц N (только числа)
    rf"|(?:{_ALL_UNITS})\s+через\s+{_NUM_PAT}\b"                   # единиц через N (только числа)
    r"|через\s+час\b|через\s+минуту\b|через\s+неделю\b"
    r"|\bзавтра\b|\bпослезавтра\b|\bсегодня\b"
    rf"|{_WEEKDAY_STRIP}"
    r"|\bв\s+\d{1,2}(?::\d{2})?\s*(?:утра|дня|вечера|ночи|час(?:ов|а)?(?:\s+(?:утра|дня|вечера|ночи))?)?"
    rf"|\b(?:{_TOD_PAT})\b"
    rf"|за\s+{_NUM_PAT}\s+(?:{_ALL_UNITS})\s+до(?:\s+(?!в\s+\d)[а-яёА-ЯЁ]+){{0,6}}"  # за N до [чего-то]
)

_TIME_STRIP = (
    r"(?:(?:завтра|сегодня)\s+)?в\s+\d{1,2}(?::\d{2})?\s*(?:утра|дня|вечера|ночи|час(?:ов|а)?(?:\s+(?:утра|дня|вечера|ночи))?)?"
    rf"|\b(?:{_TOD_PAT})\b"
    r"|\bв\s+обед\b"
    r"|\bсегодня\b|\bзавтра\b|\bпослезавтра\b"
    rf"|{_WEEKDAY_STRIP}"
    r"|\b\d{1,2}(?:-?го|-?е|-?ое|-?ого)?\s+числ[а-яё]*\b"
    r"|(?:двадцать\s+(?:первого|второго|третьего|четвёртого|четвертого|пятого|шестого|седьмого|восьмого|девятого)"
    r"|тридцать\s+первого"
    r"|первого|второго|третьего|четвёртого|четвертого|пятого|шестого|седьмого|восьмого|девятого|десятого"
    r"|одиннадцатого|двенадцатого|тринадцатого|четырнадцатого|пятнадцатого|шестнадцатого"
    r"|семнадцатого|восемнадцатого|девятнадцатого|двадцатого|тридцатого)"
    r"\s+числ[а-яё]*\b"
)

def _extract_message(text: str) -> str:
    """Убирает служебные слова и возвращает суть напоминания."""
    result = text
    # "ставь/хочу/поставь/добавь напоминание/напоминалку"
    result = re.sub(
        r"(можешь\s+)?(ставь|хочу|поставь|добавь|нужно|поставить|добавить|мне\s+нужно)\s+напомина\w+\s*",
        "", result, flags=re.IGNORECASE
    )
    # "будильник [на]"
    result = re.sub(r"\bбудильник(?:\s+на)?\b", "", result, flags=re.IGNORECASE)
    # "не забудь напомнить" / "хочу чтобы ты напомнил[а]"
    result = re.sub(
        r"не\s+забудь\s+напомнить\b|хочу\s+чтобы\s+ты\s+напомни\w+",
        "", result, flags=re.IGNORECASE
    )
    # Всё до и включая основной триггер
    # Убираем мусорные слова-паразиты
    result = re.sub(r"\b(пожалуйста|блин|бля|ну|вот|типа|короч|кстати|просто|давай|ровно)\b", "", result, flags=re.IGNORECASE)

    trigger_pat = re.compile(
        r"^(.*?)\b(напомни|напоминай|напомнить|напомнил[аи]?|дай\s+знать|нужно\s+напомнить)\b[-\s]*(ка\b\s*)?\s*(мне\s*)?(пожалуйста\s*)?",
        re.IGNORECASE
    )
    m = trigger_pat.match(result)
    if m:
        before = m.group(1).strip(" ,.")
        after = result[m.end():].strip(" ,.")
        # Убираем лишние пробелы
        after = re.sub(r"\s{2,}", " ", after).strip(" ,.")
        before = re.sub(r"\s{2,}", " ", before).strip(" ,.")
        result = after if after else before
    else:
        result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" ,.!?…")

# EN parsing

_RE_LATIN    = re.compile(r'[a-zA-Z]')
_RE_CYRILLIC = re.compile(r'[а-яёА-ЯЁ]')

def _detect_lang(text: str) -> str:
    return 'en' if len(_RE_LATIN.findall(text)) > len(_RE_CYRILLIC.findall(text)) else 'ru'

EN_UNITS = {
    "second": 1,  "seconds": 1,  "sec": 1,  "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour":  3600,"hours":  3600,"hr":  3600,"hrs":  3600,
    "day":  86400,"days":  86400,
    "week": 604800,"weeks": 604800,
    "month": 2592000,"months": 2592000,
}

EN_WORD_NUMS = {
    "a": 1,"an": 1,"one": 1,"two": 2,"three": 3,"four": 4,"five": 5,
    "six": 6,"seven": 7,"eight": 8,"nine": 9,"ten": 10,
    "fifteen": 15,"twenty": 20,"thirty": 30,"couple": 2,"few": 3,"half": 0.5,
}

EN_TOD = {
    "in the morning": 8, "morning": 8,
    "in the afternoon": 13, "afternoon": 13, "at lunch": 13, "lunch": 13,
    "in the evening": 17, "evening": 17,
    "at night": 22, "night": 22, "tonight": 22,
    "at noon": 12, "noon": 12,
    "at midnight": 0, "midnight": 0,
}

EN_WEEKDAYS = {
    "monday": 0,"mon": 0,
    "tuesday": 1,"tue": 1,"tues": 1,
    "wednesday": 2,"wed": 2,
    "thursday": 3,"thu": 3,"thur": 3,"thurs": 3,
    "friday": 4,"fri": 4,
    "saturday": 5,"sat": 5,
    "sunday": 6,"sun": 6,
}

_EN_UNIT_PAT = "|".join(sorted(EN_UNITS, key=len, reverse=True))
_EN_WD_PAT   = "|".join(sorted(EN_WEEKDAYS, key=len, reverse=True))

def _parse_en_num(s: str) -> float | None:
    s = s.strip().lower()
    if s in EN_WORD_NUMS:
        return EN_WORD_NUMS[s]
    try:
        return float(s)
    except ValueError:
        return None

def _resolve_en_tod(lower: str) -> int | None:
    for phrase, h in sorted(EN_TOD.items(), key=lambda x: -len(x[0])):
        if re.search(r'\b' + re.escape(phrase) + r'\b', lower):
            return h
    return None

def _resolve_en_time(base_day: datetime, lower: str) -> float | None:
    # am/pm первым — иначе "7:00 pm" матчится как "7:00" (AM)
    m = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', lower)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == 'pm' and h != 12: h += 12
        elif m.group(3) == 'am' and h == 12: h = 0
        return base_day.replace(hour=h, minute=mn, second=0, microsecond=0).timestamp()
    m = re.search(r'\bat\s+(\d{1,2}):(\d{2})\b', lower)
    if m:
        return base_day.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0).timestamp()
    m = re.search(r'\bat\s+(\d{1,2})\b', lower)
    if m and 0 <= int(m.group(1)) <= 23:
        return base_day.replace(hour=int(m.group(1)), minute=0, second=0, microsecond=0).timestamp()
    h = _resolve_en_tod(lower)
    if h is not None:
        return base_day.replace(hour=h, minute=0, second=0, microsecond=0).timestamp()
    return None

def _parse_en_delta(text: str, tz: zoneinfo.ZoneInfo) -> float | None:
    lower = text.lower().strip()

    if re.search(r'\bday\s+after\s+tomorrow\b', lower):
        d = datetime.now(tz=tz) + timedelta(days=2)
        ts = _resolve_en_time(d, lower)
        return ts if ts is not None else time.time() + 172800

    if re.search(r'\btomorrow\b', lower):
        d = datetime.now(tz=tz) + timedelta(days=1)
        ts = _resolve_en_time(d, lower)
        return ts if ts is not None else time.time() + 86400

    if re.search(r'\btoday\b', lower):
        d = datetime.now(tz=tz)
        ts = _resolve_en_time(d, lower)
        if ts is not None:
            if ts < time.time(): ts += 86400
            return ts
        return None

    m_next = re.search(r'\bnext\s+(' + _EN_WD_PAT + r')\b', lower)
    if m_next:
        wd = EN_WEEKDAYS[m_next.group(1)]
        now = datetime.now(tz=tz)
        days_ahead = (wd - now.weekday()) % 7 or 7
        days_ahead += 7
        d = now + timedelta(days=days_ahead)
        ts = _resolve_en_time(d, lower)
        return ts if ts is not None else d.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()

    m_wd = re.search(r'\b(?:on\s+)?(' + _EN_WD_PAT + r')\b', lower)
    if m_wd:
        wd = EN_WEEKDAYS[m_wd.group(1)]
        d = _next_weekday(wd, tz)
        ts = _resolve_en_time(d, lower)
        return ts if ts is not None else d.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()

    # "in a couple (of) hours" / "in a few days"
    m_couple = re.search(rf'\bin\s+(?:a\s+)?(?:couple|few)(?:\s+of)?\s+({_EN_UNIT_PAT})\b', lower)
    if m_couple and m_couple.group(1) in EN_UNITS:
        num = 2 if 'couple' in lower else 3
        return time.time() + num * EN_UNITS[m_couple.group(1)]

    # "in X units"
    m = re.search(rf'\bin\s+([\w.]+)\s+({_EN_UNIT_PAT})\b', lower)
    if m:
        num = _parse_en_num(m.group(1))
        unit = m.group(2)
        if num is not None and unit in EN_UNITS:
            return time.time() + num * EN_UNITS[unit]

    # "in an hour" / "in a minute" etc (no explicit number word)
    m2 = re.search(rf'\bin\s+(a|an)\s+({_EN_UNIT_PAT})\b', lower)
    if m2 and m2.group(2) in EN_UNITS:
        return time.time() + EN_UNITS[m2.group(2)]

    return None

def _parse_en_absolute(text: str, tz: zoneinfo.ZoneInfo) -> float | None:
    lower = text.lower().strip()

    def _tod_tomorrow(h: int, mn: int = 0, nearest: bool = False) -> float:
        now = datetime.now(tz=tz)
        t = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if t.timestamp() >= time.time():
            return t.timestamp()
        # nearest=True: try pm counterpart (for bare "at 9" without am/pm)
        if nearest and 1 <= h <= 11:
            t_pm = now.replace(hour=h + 12, minute=mn, second=0, microsecond=0)
            if t_pm.timestamp() >= time.time():
                return t_pm.timestamp()
        return (t + timedelta(days=1)).timestamp()

    m = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', lower)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == 'pm' and h != 12: h += 12
        elif m.group(3) == 'am' and h == 12: h = 0
        return _tod_tomorrow(h, mn)  # explicit am/pm — no nearest fallback

    m = re.search(r'\bat\s+(\d{1,2}):(\d{2})\b', lower)
    if m: return _tod_tomorrow(int(m.group(1)), int(m.group(2)))

    m = re.search(r'\bat\s+(\d{1,2})\b', lower)
    if m and 0 <= int(m.group(1)) <= 23:
        return _tod_tomorrow(int(m.group(1)), 0, nearest=True)

    h = _resolve_en_tod(lower)
    if h is not None: return _tod_tomorrow(h)

    return None

def _parse_en_interval(text: str) -> int | None:
    lower = text.lower().strip()
    if re.search(r'\bevery\s+hour\b|\bhourly\b', lower): return 3600
    if re.search(r'\bevery\s+minute\b', lower): return 60
    if re.search(r'\bevery\s+day\b|\bdaily\b', lower): return 86400
    if re.search(r'\bevery\s+week\b|\bweekly\b', lower): return 604800

    m = re.search(rf'\bevery\s+([\w.]+)\s+({_EN_UNIT_PAT})\b', lower)
    if m:
        num = _parse_en_num(m.group(1))
        unit = m.group(2)
        if num is not None and unit in EN_UNITS:
            return int(num * EN_UNITS[unit])

    m2 = re.search(rf'\bonce\s+(?:every|a|an)\s+({_EN_UNIT_PAT})\b', lower)
    if m2 and m2.group(1) in EN_UNITS:
        return EN_UNITS[m2.group(1)]

    return None

def _extract_en_message(text: str) -> str:
    result = text.strip()
    result = re.sub(
        r"(?:please\s+)?(?:remind\s+me\s+(?:to\s+)?|set\s+(?:a\s+)?reminder\s+(?:for\s+|to\s+)?|"
        r"don'?t\s+forget\s+(?:to\s+)?|let\s+me\s+know\s+(?:to\s+)?)",
        "", result, flags=re.IGNORECASE
    ).strip()
    # Strip time expressions
    strips = [
        rf'\bin\s+[\w.]+\s+(?:{_EN_UNIT_PAT})\b',
        rf'\bin\s+(?:a|an)\s+(?:{_EN_UNIT_PAT})\b',
        r'\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b',
        r'\bday\s+after\s+tomorrow\b',
        r'\btomorrow\b', r'\btoday\b',
        rf'\bnext\s+(?:{_EN_WD_PAT})\b',
        rf'\b(?:on\s+)?(?:{_EN_WD_PAT})\b',
        r'\bin\s+the\s+(?:morning|afternoon|evening)\b',
        r'\bat\s+(?:noon|midnight|lunch)\b',
        r'\bat\s+\d{1,2}\b',
        rf'\bin\s+(?:a\s+)?(?:couple|few)(?:\s+of)?\s+(?:{_EN_UNIT_PAT})\b',
        r'\b(?:tonight|morning|afternoon|evening|noon|midnight)\b',
        rf'\bevery\s+(?:[\w.]+\s+)?(?:{_EN_UNIT_PAT})\b',
        r'\bevery\s+hour\b|\bevery\s+minute\b|\bevery\s+day\b',
        r'\bdaily\b|\bweekly\b|\bhourly\b',
    ]
    for pat in strips:
        result = re.sub(pat, "", result, flags=re.IGNORECASE)
    result = re.sub(r'\s+', ' ', result).strip(' ,.!?')
    result = re.sub(r'^to\s+', '', result, flags=re.IGNORECASE).strip(' ,.!?')
    return result or "reminder"

_EN_BARE_HOUR = re.compile(r'\bat\s+(\d{1,2})\b(?!\s*(?:am|pm|:\d))', re.IGNORECASE)

def _parse_en(text: str, tz: zoneinfo.ZoneInfo) -> dict:
    result = {"type": None, "message": None, "interval_seconds": None, "next_fire": None, "error": None}
    lower = text.lower().strip()

    # Bare "at N" (≤12) without am/pm — ambiguous, ask to clarify
    m_bare = _EN_BARE_HOUR.search(lower)
    if m_bare and int(m_bare.group(1)) <= 12:
        h = int(m_bare.group(1))
        result["error"] = (
            f"Did you mean {h}:00 AM or {h}:00 PM?\n"
            f"Please add AM or PM, e.g.: «at {h} AM» or «at {h} PM»"
        )
        return result

    is_recurring = bool(re.search(
        r'\bevery\s+|\bdaily\b|\bweekly\b|\bhourly\b|remind\s+me\s+every|once\s+(?:a|every)',
        lower
    ))

    if is_recurring:
        interval = _parse_en_interval(lower)
        if interval is None:
            result["error"] = "I didn't understand the interval. Try: «remind me every 2 hours» or «every 30 minutes drink water»"
            return result
        first_fire = _parse_en_absolute(text, tz)
        msg = _extract_en_message(text)
        result["type"] = "recurring"
        result["interval_seconds"] = interval
        result["next_fire"] = first_fire
        result["message"] = msg
        return result

    is_once = bool(re.search(
        r"remind\s+me\b|set\s+(?:a\s+)?reminder|don'?t\s+forget|let\s+me\s+know",
        lower
    ))

    if is_once:
        # "that" как маркер контента: "remind me at 8pm that tomorrow is exam"
        _that = re.search(r'\bthat\b', lower)
        lower_for_time = lower[:_that.start()] if _that else lower
        text_for_time = text[:_that.start()] if _that else text

        next_fire = _parse_en_delta(lower_for_time, tz)
        if next_fire is None:
            next_fire = _parse_en_absolute(text_for_time, tz)
        if next_fire is None:
            result["error"] = (
                "I didn't understand the time. Try:\n"
                "• remind me in 30 minutes to drink water\n"
                "• remind me tomorrow at 9am to call mom\n"
                "• remind me every 2 hours to stretch"
            )
            return result

        if _that:
            msg = text[_that.end():].strip() or "reminder"
        else:
            msg = _extract_en_message(text)
        result["type"] = "once"
        result["next_fire"] = next_fire
        result["message"] = msg
        return result

    result["error"] = (
        "I didn't understand. Try:\n"
        "• remind me in 30 minutes to drink water\n"
        "• remind me tomorrow at 9am\n"
        "• remind me every 2 hours to drink water"
    )
    return result

def parse(text: str, user_tz: str | None = None) -> dict:
    text_clean = text.strip()
    tz = _get_tz(user_tz)

    if _detect_lang(text_clean) == 'en':
        return _parse_en(text_clean, tz)

    lower = _normalize(text_clean.lower())

    result = {
        "type": None,
        "message": None,
        "interval_seconds": None,
        "next_fire": None,
        "error": None,
    }

    is_recurring = bool(re.search(
        r"напоминай|\bставь напоминание|добавь напоминание|хочу чтобы ты напоминал|"
        r"напоминалку каждый|напоминалку каждые",
        lower
    )) or bool(re.search(r"каждый|каждые|каждую|раз\s+в\s+", lower))

    if is_recurring:
        interval = _parse_interval(lower)
        if interval is None:
            result["error"] = (
                "Не понял интервал. Попробуй: «напоминай вставать каждый час» "
                "или «напоминай пить воду каждые 30 минут»"
            )
            return result

        text_norm_rec = _normalize(text_clean)
        msg = re.sub(
            r"каждый\s+час|каждую\s+минуту|каждую\s+секунду|каждый\s+день|каждые\s+сутки|каждую\s+неделю|"
            r"раз\s+в\s+[\wа-яё]+\s+(?:дн[яейи]|день|дней|сутки?|недел[юьи]|недель|месяц[а-ев]?)\b|"
            r"каждые?\s+[\wа-яё.,]+(?:\s+[\wа-яё]+)?\s+(?:секунд[у-ы]?|сек|минут[у-ы]?|мин|час[а-ов]*|ч|дн[яейи]|день|дней|недел[юьи]|недель)\b",
            "", text_norm_rec, flags=re.IGNORECASE
        )
        # first fire time if specified
        first_fire = _parse_once_absolute(text_clean, tz)

        msg = re.sub(_TIME_STRIP, "", msg, flags=re.IGNORECASE)
        msg = _extract_message(msg)
        result["type"] = "recurring"
        result["interval_seconds"] = interval
        result["next_fire"] = first_fire
        result["message"] = msg or "напоминание"
        return result

    is_once = bool(re.search(
        r"напомни\b|хочу напомина\w+|поставь напомина\w+|поставить напомина\w+|"
        r"добавь напомина\w+|добавить напомина\w+|"
        r"не забудь напомнить|напомнить\b|напомнил[аи]?\b|нужно напомнить|"
        r"нужно напомина\w+|мне нужно напомина\w+|"
        r"хочу чтобы ты напомнил|можешь напомнить|можешь поставить напомина\w+|"
        r"дай знать",
        lower
    ))

    if is_once:
        # normalize
        text_norm = _normalize(text_clean)

        _content_marker = re.search(r'\bо\s+том,?\s+что\b|\bпро\s+то,?\s+что\b', lower)
        # "что" as separator only if time follows
        _chto_marker = re.search(
            r'\bчто\s+(?:завтра|послезавтра|сегодня|через\s+\d|через\s+[а-яё]'
            r'|в\s+\d|в\s+(?:' + _WEEKDAY_PAT + r')|утром|вечером|днём|ночью)',
            lower
        ) if not _content_marker else None

        if _content_marker:
            lower_for_time = lower[:_content_marker.start()]
            text_for_abs   = text_clean[:_content_marker.start()]
        elif _chto_marker:
            lower_for_time = lower[:_chto_marker.start()]
            text_for_abs   = text_clean[:_chto_marker.start()]
        else:
            lower_for_time = lower
            text_for_abs   = text_clean

        next_fire = _parse_once_delta(lower_for_time, tz)

        _named_day = bool(
            re.search(r'\bзавтра\b|\bпослезавтра\b|\bсегодня\b|\bследующ|\bчисл', lower_for_time) or
            re.search(r'\bв\s+(?:' + _WEEKDAY_PAT + r')\b', lower_for_time)
        )

        _chto_cut = (_chto_marker.start() + 4) if _chto_marker else None  # 4 = len("что ")

        if next_fire is None:
            next_fire = _parse_once_absolute(text_for_abs, tz)
            if _chto_marker and next_fire is not None:
                msg = text_clean[_chto_cut:].strip()
            else:
                msg = re.sub(_TIME_STRIP, "", text_norm, flags=re.IGNORECASE)
        elif _chto_marker:
            msg = text_clean[_chto_cut:].strip()
        elif _named_day:
            if _content_marker:
                content_m_norm = re.search(r'\bо\s+том,?\s+что\b|\bпро\s+то,?\s+что\b', text_norm, re.IGNORECASE)
                if content_m_norm:
                    prefix = re.sub(_TIME_STRIP, "", text_norm[:content_m_norm.start()], flags=re.IGNORECASE)
                    msg = prefix + text_norm[content_m_norm.start():]
                else:
                    msg = re.sub(_TIME_STRIP, "", text_norm, flags=re.IGNORECASE)
            else:
                msg = re.sub(_TIME_STRIP, "", text_norm, flags=re.IGNORECASE)
        else:
            msg = re.sub(_DELTA_STRIP, "", text_norm, flags=re.IGNORECASE)

        if next_fire is None:
            result["error"] = (
                "Не понял время. Попробуй: «напомни через 30 минут выйти» "
                "или «напомни в 15:00 позвонить»"
            )
            return result

        msg = _extract_message(msg)
        result["type"] = "once"
        result["next_fire"] = next_fire
        result["message"] = msg or "напоминание"
        return result

    result["error"] = (
        "Не понял команду.\n\n"
        "Примеры:\n"
        "• напомни через 30 минут выйти погулять\n"
        "• напомни в 15:00 позвонить маме\n"
        "• напоминай вставать каждый час\n"
        "• напоминай пить воду каждые 45 минут\n\n"
        "Или напиши «мои напоминания» чтобы посмотреть список."
    )
    return result