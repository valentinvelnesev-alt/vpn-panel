# Контекст сборки — корень репозитория (нужен shared/).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# postgresql-client — для pg_dump в авто-бэкапах.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Слой зависимостей отдельно от кода: правка исходников не пересобирает pip.
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir . && rm pyproject.toml

COPY backend/ ./
COPY shared/ ./shared/

RUN useradd --system --uid 10001 panel \
    && mkdir -p /app/backups /app/uploads \
    && chown -R panel:panel /app
USER panel

EXPOSE 8000

# Миграции накатываются на старте — обновление образа не требует ручных шагов.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
