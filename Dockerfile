FROM python:3.11-slim

# Часовой пояс сервиса — Москва (расписание опроса задаётся в MSK).
ENV TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tzdata нужна планировщику для зоны Europe/Moscow.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — слой кешируется, пока requirements.txt не меняется.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY tools/ ./tools/
COPY data/ ./data/

# Данные и готовые отчёты живут на постоянном томе (см. amvera.yml).
# Значения по умолчанию можно переопределить переменными окружения.
ENV DB_PATH=/data/archive.sqlite3 \
    OUTPUT_DIR=/data/out
RUN mkdir -p /data/out

# Секреты (LERS_API_KEY, ONEC_PASSWORD, MAX_BOT_TOKEN и т.д.) передаются
# переменными окружения на этапе запуска, в образ НЕ зашиваются.
CMD ["python", "-m", "app.main"]
