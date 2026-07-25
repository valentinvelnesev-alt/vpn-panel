# Контекст сборки — корень репозитория (нужен shared/).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY bot/pyproject.toml ./
RUN pip install --no-cache-dir . && rm pyproject.toml

COPY bot/ ./
COPY shared/ ./shared/

RUN useradd --system --uid 10002 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-m", "app.main"]
