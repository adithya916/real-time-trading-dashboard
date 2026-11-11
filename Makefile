# Common tasks. On Windows, run them from WSL, or copy the command under each target.
COMPOSE ?= docker compose

.PHONY: help up down logs config fmt

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
