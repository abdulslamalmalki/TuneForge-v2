FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KNOWLEDGE_BUILDER_DATA_DIR=/data \
    TUNEFORGE_BACKUPS_DIR=/backups \
    TUNEFORGE_ENVIRONMENT=production \
    TUNEFORGE_AUTH_MODE=required \
    TUNEFORGE_AUTH_COOKIE_SECURE=1 \
    TUNEFORGE_RATE_LIMIT_ENABLED=1 \
    TUNEFORGE_LOG_FORMAT=json \
    TUNEFORGE_SQLITE_JOURNAL_MODE=wal

WORKDIR /app

RUN addgroup --system tuneforge && adduser --system --ingroup tuneforge tuneforge

COPY pyproject.toml requirements.lock README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY tools ./tools

RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.lock \
    && pip install --no-cache-dir --disable-pip-version-check --no-deps . \
    && mkdir -p /data /backups \
    && chown tuneforge:tuneforge /data /backups

USER tuneforge
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3)"

STOPSIGNAL SIGTERM

CMD ["sh", "-c", "python -m tools.check_production && python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips \"${TUNEFORGE_FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
