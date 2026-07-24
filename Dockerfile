FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KNOWLEDGE_BUILDER_DATA_DIR=/data \
    TUNEFORGE_ENVIRONMENT=production \
    TUNEFORGE_AUTH_MODE=required \
    TUNEFORGE_AUTH_COOKIE_SECURE=1

WORKDIR /app

RUN addgroup --system tuneforge && adduser --system --ingroup tuneforge tuneforge

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY tools ./tools

RUN pip install --no-cache-dir . && mkdir -p /data && chown tuneforge:tuneforge /data

USER tuneforge
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3)"

CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
