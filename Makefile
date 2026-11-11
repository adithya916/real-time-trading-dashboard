# Common tasks. On Windows, run them from WSL, or copy the command under each target.
COMPOSE ?= docker compose

.PHONY: help up down logs config fmt lint test check

help: ## List targets
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

up: ## Build and start the stack, wait until healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Follow the logs
	$(COMPOSE) logs -f

config: ## Validate the Compose files
	$(COMPOSE) config --quiet

fmt: ## Run every formatter and lint hook on the whole tree
	pre-commit run --all-files

lint: ## Lint Python and the web app
	uvx ruff@0.8.4 check .
	uvx ruff@0.8.4 format --check .
	cd services/legacy && uv run mypy .
	cd web && npm run lint && npm run format:check

test: ## Run the test suites
	cd services/legacy && uv run pytest -q

check: lint test ## Everything CI runs before a merge, except the Docker integration job
