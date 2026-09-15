BACKEND := apps/backend/sentinel
FRONTEND := apps/frontend/sentinel
DESKTOP := apps/desktop/sentinel
TUI := apps/tui

.DEFAULT_GOAL := help

.PHONY: help dev tui logs setup lint format test typecheck check desktop-build version

help:
	@echo "make setup         Install the complete local development environment"
	@echo "make dev           Open Electron with live frontend/backend reload"
	@echo 'make tui ARGS="--model MODEL"  Open the standalone terminal chat'
	@echo "make logs          Follow local desktop and backend service logs"
	@echo "make check         Run formatting, lint, tests, and TypeScript checks"
	@echo "make desktop-build Build the distributable desktop app"
	@echo "make version VERSION=2.0.0  Set the app release version and lock metadata"
	@echo "Quit Electron or press Ctrl+C in make dev to stop; data is preserved."

setup:
	bash scripts/setup-dev.sh

version:
	@test -n "$(VERSION)" || (echo 'Usage: make version VERSION=2.0.0'; exit 1)
	bash scripts/sync-version.sh --set "$(VERSION)"

dev:
	npm --prefix $(DESKTOP) run dev

tui:
	uv run --locked --project $(TUI) sentinel-tui $(ARGS)

logs:
	tail -F "$(HOME)/Library/Application Support/Sentinel Dev/logs/desktop-$$(date -u +%F).log"

lint:
	cd $(BACKEND) && uv run --locked black --check app tests scripts
	cd $(BACKEND) && uv run --locked ruff check app tests scripts

format:
	cd $(BACKEND) && uv run --locked black app tests scripts

test:
	cd $(BACKEND) && uv run --locked pytest tests/ -q
	npm --prefix $(DESKTOP) test

typecheck:
	cd $(FRONTEND) && npx --no-install tsc -b
	npm --prefix $(DESKTOP) run desktop:verify

check: lint test typecheck

desktop-build:
	npm --prefix $(DESKTOP) run desktop:build
