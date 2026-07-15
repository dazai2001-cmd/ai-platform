# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.11-slim AS runtime

ARG APP_VERSION=dev
ARG GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    PORT=5000 \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA} \
    XDG_CACHE_HOME=/app/data/cache \
    HF_HOME=/app/data/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/data/cache/sentence-transformers

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ghostscript \
        libgomp1 \
        ocrmypdf \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .

RUN mkdir -p data/raw data/uploads data/indexes data/processed data/cache \
    && chown -R app:app /app/data

USER app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health/live', timeout=4).read()"]

# One worker is intentional while FAISS and background-job coordination remain
# local process state. Threads provide concurrency without divergent indexes.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers ${GUNICORN_WORKERS:-1} --worker-class gthread --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-210} --graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30} --access-logfile /dev/null --error-logfile - 'apps.api.main:create_app()'"]
