.PHONY: up down logs test coverage lint format typecheck check migrate makemigrations seed schema postman shell docker-build docker-check

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f web worker

test:
	uv run pytest

coverage:
	uv run pytest --cov=apps --cov-report=term-missing --cov-fail-under=85

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy .

check: lint typecheck coverage
	uv run python manage.py spectacular --file openapi.yaml --validate --fail-on-warn

migrate:
	uv run python manage.py migrate

makemigrations:
	uv run python manage.py makemigrations

seed:
	uv run python manage.py seed_demo

schema:
	uv run python manage.py spectacular --file openapi.yaml --validate --fail-on-warn

postman: schema
	uv run python scripts/build_postman.py openapi.yaml postman.json

shell:
	uv run python manage.py shell

docker-build:
	docker compose build

docker-check:
	DJANGO_SETTINGS_MODULE=config.settings.production \
	DJANGO_SECRET_KEY=build-check-only \
	DJANGO_ALLOWED_HOSTS=api.example.com \
	DATABASE_URL=postgres://u:p@localhost:5432/db \
	REDIS_URL=redis://localhost:6379/0 \
	CELERY_BROKER_URL=redis://localhost:6379/1 \
	AWS_STORAGE_BUCKET_NAME=check-bucket \
	FIELD_ENCRYPTION_KEYS=tW8uBgbY93Ih64CaqRsEIDheNMVimjTE97KQKAyVAxA= \
	CSRF_TRUSTED_ORIGINS=https://app.example.com \
	EMAIL_BACKEND=anymail.backends.mailgun.EmailBackend \
	CAPTCHA_VERIFIER=apps.core.captcha.TurnstileCaptchaVerifier \
	CAPTCHA_SECRET_KEY=ci-captcha-secret \
	uv run python manage.py check --deploy
