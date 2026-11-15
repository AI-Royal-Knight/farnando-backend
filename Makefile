# Adelgaceya backend. Run `make` (or `make help`) to list every target.
#
# Two ways to run the API:
#   Docker (default)  make up / make rebuild — the image is baked (no source mount), so code
#                     and migration changes need `make rebuild`; the web container migrates
#                     itself on start.
#   Host (uv)         make dev — Django runs from your checkout against the compose Postgres,
#                     Redis and Mailpit (start them with `make services`).

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
MANAGE  := $(COMPOSE) exec web python manage.py
APP_SERVICES := web worker beat

# Host ports: what scripts/compose-up.sh wrote to .env, else the compose defaults.
env_port = $(or $(shell grep -E '^$(1)=' .env 2>/dev/null | tail -n 1 | cut -d= -f2 | cut -d' ' -f1),$(2))
POSTGRES_PORT := $(call env_port,POSTGRES_PORT,5435)
REDIS_PORT    := $(call env_port,REDIS_PORT,6381)
MAILPIT_SMTP  := $(call env_port,MAILPIT_SMTP_PORT,1025)
DEV_PORT      ?= 8000

# Environment for host runs against the compose services (see `make dev`).
DEV_ENV := DJANGO_SETTINGS_MODULE=config.settings.local \
	DATABASE_URL=postgres://adelgaceya:adelgaceya@localhost:$(POSTGRES_PORT)/adelgaceya \
	REDIS_URL=redis://localhost:$(REDIS_PORT)/0 \
	CELERY_BROKER_URL=redis://localhost:$(REDIS_PORT)/1 \
	CELERY_RESULT_BACKEND=redis://localhost:$(REDIS_PORT)/2 \
	EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend \
	EMAIL_HOST=localhost EMAIL_PORT=$(MAILPIT_SMTP)
DEV_MANAGE := $(DEV_ENV) uv run python manage.py

.PHONY: help install setup up down stop restart rebuild build services ps logs logs-web logs-worker \
	migrate makemigrations migrations showmigrations seed superuser shell dbshell manage reset-db \
	dev dev-migrate dev-seed dev-shell dev-worker \
	test coverage lint format typecheck check schema postman docker-build docker-check

help: ## List the targets
	@awk 'BEGIN {FS = ":.*?## "} /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
		/^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "  First run:  make setup     Run a command:  make manage CMD=\"check\""

##@ Setup

install: ## Install Python dependencies with uv (host tooling, tests, linters)
	uv sync

setup: ## First run: copy .env.example, start the stack, migrate, seed demo users
	@[ -f .env ] || { cp .env.example .env && echo "Created .env from .env.example"; }
	$(MAKE) up
	$(MAKE) migrate
	$(MAKE) seed

##@ Docker stack

up: ## Build and start everything (picks free host ports, see scripts/compose-up.sh)
	./scripts/compose-up.sh

down: ## Stop and remove the containers (data volumes are kept)
	$(COMPOSE) down

stop: ## Stop the containers without removing them
	$(COMPOSE) stop

restart: ## Restart web, worker and beat (no rebuild: picks up .env changes only)
	$(COMPOSE) up -d --force-recreate $(APP_SERVICES)

rebuild: ## Rebuild the image and restart web/worker/beat (code or migration changes)
	$(COMPOSE) build web
	$(COMPOSE) up -d $(APP_SERVICES)

build: ## Build the image only
	$(COMPOSE) build web

services: ## Start only Postgres, Redis, MinIO and Mailpit (for `make dev`)
	$(COMPOSE) up -d postgres redis minio mailpit

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow web and worker logs
	$(COMPOSE) logs -f web worker

logs-web: ## Follow web logs only
	$(COMPOSE) logs -f web

logs-worker: ## Follow worker and beat logs
	$(COMPOSE) logs -f worker beat

##@ Database and Django (inside the running web container)

migrate: ## Apply migrations in the container (new migration files need `make rebuild` first)
	$(MANAGE) migrate

makemigrations: ## Create migration files from your checkout (host, no database needed)
	uv run python manage.py makemigrations $(ARGS)

migrations: makemigrations rebuild ## makemigrations, then rebuild so the container applies them
	@echo "Migrations created and applied (web runs migrate on start)."

showmigrations: ## List migrations and whether they are applied
	$(MANAGE) showmigrations $(ARGS)

seed: ## Create the demo users (idempotent; password: Adelgaceya2026!)
	$(MANAGE) seed_demo

superuser: ## Create a Django admin superuser (interactive)
	$(COMPOSE) exec web python manage.py createsuperuser

shell: ## Django shell in the container
	$(COMPOSE) exec web python manage.py shell

dbshell: ## psql in the Postgres container
	$(COMPOSE) exec postgres psql -U adelgaceya -d adelgaceya

manage: ## Any manage.py command: make manage CMD="load_ubigeo ubigeo.csv"
	@[ -n "$(CMD)" ] || { echo 'Usage: make manage CMD="<command> [args]"'; exit 2; }
	$(MANAGE) $(CMD)

reset-db: ## DESTRUCTIVE: wipe Postgres/Redis/MinIO volumes, then setup again
	@read -r -p "This deletes all local data (database, files, queues). Type 'yes' to continue: " answer; \
		[ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	$(COMPOSE) down -v
	$(MAKE) up
	$(MAKE) migrate
	$(MAKE) seed

##@ Host development (uv, against the compose services)

dev: ## Run the dev server from your checkout on DEV_PORT (default 8000; stop Docker web first)
	$(DEV_MANAGE) runserver 0.0.0.0:$(DEV_PORT)

dev-migrate: ## Apply migrations from your checkout to the compose database
	$(DEV_MANAGE) migrate

dev-seed: ## Seed demo users from your checkout
	$(DEV_MANAGE) seed_demo

dev-shell: ## Django shell from your checkout
	$(DEV_MANAGE) shell

dev-worker: ## Celery worker + beat from your checkout
	$(DEV_ENV) uv run celery -A config worker -B -l info

##@ Quality gates (host)

test: ## Run the test suite
	uv run pytest $(ARGS)

coverage: ## Tests with the coverage gate (>= 85%)
	uv run pytest --cov=apps --cov-report=term-missing --cov-fail-under=85

lint: ## ruff check + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Format and auto-fix with ruff
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy
	uv run mypy .

check: lint typecheck coverage ## Everything CI runs, plus the OpenAPI schema
	uv run python manage.py spectacular --file openapi.yaml --validate --fail-on-warn

schema: ## Regenerate and validate openapi.yaml
	uv run python manage.py spectacular --file openapi.yaml --validate --fail-on-warn

postman: schema ## Regenerate postman.json from the schema
	uv run python scripts/build_postman.py openapi.yaml postman.json

docker-build: ## Build every compose image
	$(COMPOSE) build

docker-check: ## Production `check --deploy` with placeholder settings (zero warnings)
	DJANGO_SETTINGS_MODULE=config.settings.production \
	DJANGO_SECRET_KEY=build-check-only-long-random-placeholder-0123456789abcdef \
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
	PAYMENT_PROVIDER=apps.assessments.payments.CulqiPaymentProvider \
	CULQI_SECRET_KEY=sk_test_placeholder \
	uv run python manage.py check --deploy
