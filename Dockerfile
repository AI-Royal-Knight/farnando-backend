# syntax=docker/dockerfile:1.7

FROM python:3.15-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# collectstatic is a build step. The dummy values are compile-time only and are
# never copied into the runtime image as secrets.
ENV DJANGO_SETTINGS_MODULE=config.settings.local \
    DJANGO_DEBUG=False \
    DJANGO_SECRET_KEY=build-only-not-a-secret \
    DATABASE_URL=postgres://build:build@127.0.0.1:5432/build \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    CELERY_BROKER_URL=redis://127.0.0.1:6379/1 \
    FIELD_ENCRYPTION_KEYS=tW8uBgbY93Ih64CaqRsEIDheNMVimjTE97KQKAyVAxA= \
    EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend \
    AWS_STORAGE_BUCKET_NAME=build-only
RUN python manage.py collectstatic --noinput


FROM python:3.15-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--config", "config/gunicorn.py"]
