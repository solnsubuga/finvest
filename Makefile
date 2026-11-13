SHELL := /bin/sh
.DEFAULT_GOAL := help
.PHONY: help bootstrap dev dev-stop dev-down dev-logs dev-status check-backend test-integration test-journeys audit test-stack

# A PostgreSQL administrator URL for integration tests. Left unset, `make test-integration`
# starts the Compose database and uses it.
FINVEST_TEST_ADMIN_URL ?=
FINVEST_DB_PORT ?= 5432

help:
	@printf '%s\n' \
	  'Run:    make dev, dev-stop, dev-down, dev-logs, dev-status' \
	  'Checks: make bootstrap, check-backend, test-integration, test-journeys, test-stack, audit'

bootstrap:
	uv sync --project backend --locked

dev:
	docker compose up --build

dev-stop:
	docker compose stop

dev-down:
	docker compose down

dev-logs:
	docker compose logs --follow --tail=100

dev-status:
	docker compose ps --all

# Formatting, lint, types, import boundaries and the tests that need no database.
check-backend:
	cd backend && uv run --locked ruff format --check .
	cd backend && uv run --locked ruff check .
	cd backend && uv run --locked mypy
	cd backend && uv run --locked lint-imports
	cd backend && uv run --locked pytest -m 'not integration and not journey'

# Every backend test, against real PostgreSQL with the committed migrations and the real roles.
test-integration:
ifeq ($(strip $(FINVEST_TEST_ADMIN_URL)),)
	docker compose up --detach --wait db
	cd backend && FINVEST_TEST_ADMIN_URL=postgresql://postgres:local-admin@127.0.0.1:$(FINVEST_DB_PORT)/postgres \
	  uv run --locked pytest -m integration
else
	cd backend && FINVEST_TEST_ADMIN_URL='$(FINVEST_TEST_ADMIN_URL)' uv run --locked pytest -m integration
endif

# The end-to-end journeys: API, worker and scheduler on a database of their own, with the
# sandbox ioTec and a recording email and SMS provider.
test-journeys:
ifeq ($(strip $(FINVEST_TEST_ADMIN_URL)),)
	docker compose up --detach --wait db
	cd backend && FINVEST_TEST_ADMIN_URL=postgresql://postgres:local-admin@127.0.0.1:$(FINVEST_DB_PORT)/postgres \
	  uv run --locked pytest -m journey tests/journeys
else
	cd backend && FINVEST_TEST_ADMIN_URL='$(FINVEST_TEST_ADMIN_URL)' uv run --locked pytest -m journey tests/journeys
endif

# Build the image and start all four processes, then wait for every health check.
test-stack:
	docker compose up --build --detach --wait
	docker compose ps --all
	docker compose down

audit:
	cd backend && uv run --locked pip-audit --local
