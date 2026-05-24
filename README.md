# tg-reminder-bot

Telegram-бот для напоминаний с парсингом естественного языка на русском и английском.

## Что умеет

- Разовые напоминания: `напомни в 15:00 позвонить маме`
- Повторяющиеся: `напоминай пить воду каждые 2 часа`
- День недели: `напомни в пятницу сдать отчёт`
- Слова словами: `напомни через пять минут поесть`
- Голосовые сообщения (распознавание через Whisper)
- Заметки без времени: `запомни: пароль от вайфая 12345`
- Часовой пояс по городу: `Москва`, `Питер`, `Новосибирск`
- Английский: `remind me in 30 minutes to call mom`

## Стек

- Python 3.12
- python-telegram-bot 21
- APScheduler
- PostgreSQL (psycopg2)
- dateparser, geopy, timezonefinder

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env  # заполнить значения
python bot.py
```

## Переменные окружения

```
BOT_TOKEN          — токен от @BotFather
DATABASE_URL       — postgres://...
ADMIN_ID           — твой Telegram user_id для команд /admin
GROQ_API_KEY       — для распознавания голосовых (необязательно)
DEFAULT_TIMEZONE   — Europe/Moscow по умолчанию
```

## Структура

```
bot.py         — handlers, запуск
parser.py      — парсинг русского/английского текста
scheduler.py   — APScheduler jobs
db.py          — PostgreSQL
config.py      — переменные окружения
middleware.py  — антифлуд, лимиты
admin.py       — команды для админа
city_tz.py     — определение часового пояса по городу
translations.py — строки на ru/en
test_parser.py — тесты парсера
```

## Тесты

```bash
python -X utf8 test_parser.py
```

## Деплой

Railway: настроен `railway.json` и `Procfile`, нужен persistent volume для БД.
