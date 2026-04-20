FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# База данных будет храниться в /data (persistent volume)
ENV DB_PATH=/data/reminders.db

CMD ["python", "-u", "bot.py"]
