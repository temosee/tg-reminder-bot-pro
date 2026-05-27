"""
Определяет timezone по названию города.
Поддерживает разговорные сокращения, опечатки (через Nominatim).
Поддерживает города на русском, английском и других языках.
"""

import re
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from timezonefinder import TimezoneFinder
import zoneinfo

# city aliases
_ALIASES = {
    # Русские сокращения — СНГ
    "питер": "Санкт-Петербург",
    "спб": "Санкт-Петербург",
    "мск": "Москва",
    "екб": "Екатеринбург",
    "екатер": "Екатеринбург",
    "новосиб": "Новосибирск",
    "нск": "Новосибирск",
    "нижний": "Нижний Новгород",
    "ниж новгород": "Нижний Новгород",
    "ростов": "Ростов-на-Дону",
    "хабар": "Хабаровск",
    "владик": "Владивосток",
    "влад": "Владивосток",
    "вдк": "Владивосток",
    "краснодар": "Краснодар",
    "красноярск": "Красноярск",
    "алматы": "Алматы",
    "алма-ата": "Алматы",
    "астана": "Астана",
    "нур-султан": "Астана",
    "киев": "Киев",
    "київ": "Киев",
    "минск": "Минск",
    "ташкент": "Ташкент",
    "баку": "Баку",
    "тбилиси": "Тбилиси",
    "ереван": "Ереван",
    # Английские сокращения — США
    "nyc": "New York City",
    "ny": "New York City",
    "new york": "New York City",
    "la": "Los Angeles",
    "l.a.": "Los Angeles",
    "sf": "San Francisco",
    "dc": "Washington DC",
    "chi": "Chicago",
    "philly": "Philadelphia",
    "vegas": "Las Vegas",
    "nola": "New Orleans",
    # Канада
    "van": "Vancouver",
    "mtl": "Montreal",
    "to": "Toronto",
    # Великобритания
    "london": "London",
    "uk": "London",
    "manchester": "Manchester",
    "birmingham": "Birmingham",
    # Европа
    "paris": "Paris",
    "berlin": "Berlin",
    "munich": "Munich",
    "rome": "Rome",
    "milan": "Milan",
    "madrid": "Madrid",
    "barcelona": "Barcelona",
    "amsterdam": "Amsterdam",
    "brussels": "Brussels",
    "vienna": "Vienna",
    "prague": "Prague",
    "warsaw": "Warsaw",
    "stockholm": "Stockholm",
    "oslo": "Oslo",
    "helsinki": "Helsinki",
    "copenhagen": "Copenhagen",
    "zurich": "Zurich",
    "geneva": "Geneva",
    "lisbon": "Lisbon",
    "athens": "Athens",
    "budapest": "Budapest",
    "bucharest": "Bucharest",
    # Азия
    "beijing": "Beijing",
    "peking": "Beijing",
    "shanghai": "Shanghai",
    "tokyo": "Tokyo",
    "osaka": "Osaka",
    "seoul": "Seoul",
    "bangkok": "Bangkok",
    "singapore": "Singapore",
    "dubai": "Dubai",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "delhi": "New Delhi",
    "jakarta": "Jakarta",
    "taipei": "Taipei",
    "hong kong": "Hong Kong",
    "hk": "Hong Kong",
    # Австралия / Океания
    "sydney": "Sydney",
    "melbourne": "Melbourne",
    "brisbane": "Brisbane",
    "auckland": "Auckland",
    # Латинская Америка
    "sao paulo": "São Paulo",
    "sp": "São Paulo",
    "rio": "Rio de Janeiro",
    "buenos aires": "Buenos Aires",
    "ba": "Buenos Aires",
    "bogota": "Bogotá",
    "lima": "Lima",
    "santiago": "Santiago",
    # Африка
    "cairo": "Cairo",
    "lagos": "Lagos",
    "nairobi": "Nairobi",
    "johannesburg": "Johannesburg",
    "jo'burg": "Johannesburg",
    "joburg": "Johannesburg",
    "capetown": "Cape Town",
    "cape town": "Cape Town",
}

_geolocator = Nominatim(user_agent="tg-reminder-bot/1.0")
_tf = TimezoneFinder()

_CYRILLIC = re.compile(r'[а-яёА-ЯЁ]')

_OFFSET_RE = re.compile(
    r'^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::(\d{2}))?$', re.IGNORECASE
)

# offset → IANA tz
_OFFSET_TO_IANA = {
    -720: "Etc/GMT+12", -660: "Etc/GMT+11", -600: "Etc/GMT+10",
    -540: "Etc/GMT+9",  -480: "Etc/GMT+8",  -420: "Etc/GMT+7",
    -360: "Etc/GMT+6",  -300: "Etc/GMT+5",  -240: "Etc/GMT+4",
    -180: "America/Sao_Paulo", -120: "Etc/GMT+2", -60: "Etc/GMT+1",
      0:  "UTC",
     60:  "Etc/GMT-1",   120: "Europe/Paris",  180: "Europe/Moscow",
    210:  "Asia/Tehran", 240: "Asia/Dubai",    270: "Asia/Kabul",
    300:  "Asia/Karachi",330: "Asia/Kolkata",  345: "Asia/Kathmandu",
    360:  "Asia/Dhaka",  390: "Asia/Rangoon",  420: "Asia/Bangkok",
    480:  "Asia/Shanghai", 525: "Asia/Pyongyang", 540: "Asia/Tokyo",
    570:  "Australia/Adelaide", 600: "Australia/Sydney",
    630:  "Pacific/Norfolk", 660: "Pacific/Guadalcanal",
    720:  "Pacific/Auckland", 780: "Pacific/Apia",
    840:  "Pacific/Kiritimati",
}

def _offset_to_tz(city_input: str) -> tuple[str | None, str | None]:
    """Парсит '+7', 'UTC+3', 'GMT-5', '+5:30' → (tz_str, display)."""
    m = _OFFSET_RE.match(city_input.strip())
    if not m:
        return None, None
    sign = 1 if m.group(1) == '+' else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    total_minutes = sign * (hours * 60 + minutes)
    iana = _OFFSET_TO_IANA.get(total_minutes)
    if not iana:
        return None, None
    sign_str = '+' if sign > 0 else '-'
    display = f"UTC{sign_str}{hours}" + (f":{minutes:02d}" if minutes else "")
    return iana, display

def city_to_timezone(city_input: str) -> tuple[str | None, str | None]:
    """
    Возвращает (timezone_str, display_name) или (None, None) если не найдено.
    Принимает название города (любой язык) или UTC-офсет (+7, UTC+3).
    """
    city = city_input.strip()
    city_lower = city.lower()

    # try UTC offset first
    tz_offset, display_offset = _offset_to_tz(city)
    if tz_offset:
        return tz_offset, display_offset
    if _OFFSET_RE.match(city):
        return None, None  # похоже на офсет, но невалидный — не геокодим

    # apply aliases
    alias_target = _ALIASES.get(city_lower)
    city_normalized = alias_target if alias_target else city

    # detect lang for display
    lang = "ru" if _CYRILLIC.search(city_normalized) else "en"

    try:
        location = _geolocator.geocode(city_normalized, language=lang, timeout=5)
        if not location:
            location = _geolocator.geocode(city_normalized, timeout=5)
    except GeocoderTimedOut:
        return None, None

    if not location:
        return None, None

    tz = _tf.timezone_at(lat=location.latitude, lng=location.longitude)
    if not tz:
        return None, None

    display = alias_target if alias_target else location.address.split(",")[0].strip()
    return tz, display
