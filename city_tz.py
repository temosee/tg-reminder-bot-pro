"""
Определяет timezone по названию города.
Поддерживает разговорные сокращения, опечатки (через Nominatim).
"""

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from timezonefinder import TimezoneFinder

# city aliases
_ALIASES = {
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
}

_geolocator = Nominatim(user_agent="tg-reminder-bot/1.0")
_tf = TimezoneFinder()

def city_to_timezone(city_input: str) -> tuple[str | None, str | None]:
    """
    Возвращает (timezone_str, display_name) или (None, None) если не найдено.
    display_name — красивое название для показа пользователю.
    """
    city = city_input.strip()
    # apply aliases
    city_normalized = _ALIASES.get(city.lower(), city)

    try:
        location = _geolocator.geocode(city_normalized, language="ru", timeout=5)
    except GeocoderTimedOut:
        return None, None

    if not location:
        return None, None

    tz = _tf.timezone_at(lat=location.latitude, lng=location.longitude)
    if not tz:
        return None, None

    display = location.address.split(",")[0].strip()
    return tz, display
