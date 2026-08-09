import re
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from timezonefinder import TimezoneFinder

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
    "хаба": "Хабаровск",
    "владик": "Владивосток",
    "владос": "Владивосток",
    "влад": "Владивосток",
    "вдк": "Владивосток",
    "краснодар": "Краснодар",
    "красноярск": "Красноярск",
    "красндр": "Краснодар",
    "крск": "Красноярск",
    "ебург": "Екатеринбург",
    "ёбург": "Екатеринбург",
    "екат": "Екатеринбург",
    "нино": "Нижний Новгород",
    "нижний новгород": "Нижний Новгород",
    "челяба": "Челябинск",
    "чел": "Челябинск",
    "самара": "Самара",
    "казань": "Казань",
    "казик": "Казань",
    "воронеж": "Воронеж",
    "врн": "Воронеж",
    "уфа": "Уфа",
    "пермь": "Пермь",
    "омск": "Омск",
    "тюмень": "Тюмень",
    "иркутск": "Иркутск",
    "иркут": "Иркутск",
    "сочи": "Сочи",
    "калининград": "Калининград",
    "кёниг": "Калининград",
    "кениг": "Калининград",
    "мурманск": "Мурманск",
    "волгоград": "Волгоград",
    "саратов": "Саратов",
    "тольятти": "Тольятти",
    "барнаул": "Барнаул",
    "томск": "Томск",
    "кемерово": "Кемерово",
    "сургут": "Сургут",
    "якутск": "Якутск",
    "магадан": "Магадан",
    "камчатка": "Петропавловск-Камчатский",
    "птк": "Петропавловск-Камчатский",
    "южный": "Южно-Сахалинск",
    "сахалин": "Южно-Сахалинск",
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
_HAS_LETTER = re.compile(r'[^\W\d_]', re.UNICODE)
_LOOKS_LIKE_OFFSET = re.compile(r'^(?:UTC|GMT)?\s*[+-]?\s*\d{1,2}(?::\d{2})?$', re.IGNORECASE)

def city_to_timezone(city_input: str) -> tuple[str | None, str | None]:
    """Возвращает (timezone_str, display_name) или (None, None) если город не найден."""
    city = city_input.strip(" \t.,!?:;«»\"'")
    city_lower = city.lower()

    # геокодер находит что угодно: «+7» станет станцией метро, «UTC+3» — деревней
    if len(city) < 2 or not _HAS_LETTER.search(city) or _LOOKS_LIKE_OFFSET.match(city):
        return None, None

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
