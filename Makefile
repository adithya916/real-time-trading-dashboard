# Common tasks. On Windows, run them from WSL, or copy the command under each target.
COMPOSE ?= docker compose

.PHONY: help up down logs config fmt lint test check proto bench-baseline cpp-test bench-cpp

help: ## List targets
	@grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

up: ## Build and start the stack with live feeds, wait until healthy
	$(COMPOSE) --profile feeds up -d --build --wait

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Follow the logs
	$(COMPOSE) logs -f

config: ## Validate the Compose files
	$(COMPOSE) config --quiet

fmt: ## Run every formatter and lint hook on the whole tree
	pre-commit run --all-files

lint: proto ## Lint Python and the web app
	uvx ruff@0.8.4 check .
	uvx ruff@0.8.4 format --check .
	cd services/legacy && uv run mypy .
	cd services/common && uv run mypy .
	cd services/ingest && uv run mypy .
	cd services/recorder && uv run mypy .
	npx -y @bufbuild/buf@1.47.2 lint
	cd web && npm run lint && npm run format:check

test: proto ## Run the test suites
	cd services/legacy && uv run pytest -q
	cd services/common && uv run pytest -q
	cd services/ingest && uv run pytest -q
	cd services/recorder && uv run pytest -q

proto: ## Generate Python code from proto/ (not committed)
	bash scripts/gen_proto.sh

check: lint test ## Everything CI runs before a merge, except the Docker integration job

bench-baseline: ## Measure the running stack (start it with make up first)
	cd bench && uv run python baseline/run.py --out results/baseline

cpp-test: ## Build and run the C++ tests (BOOKCORE_SANITIZE=ON for ASan + UBSan)
	cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug -DBOOKCORE_TESTS=ON
	cmake --build core/build -j
	ctest --test-dir core/build --output-on-failure

bench-cpp: ## Benchmark the C++ core and compare it with PyBook on the recorded feeds
	cmake -S core -B core/build-bench -DCMAKE_BUILD_TYPE=Release -DBOOKCORE_TESTS=OFF -DBOOKCORE_BENCH=ON
	cmake --build core/build-bench -j
	cd core && uv sync --no-editable && uv run --no-sync python ../bench/cpp/run.py --bench-binary build-bench/bookcore_bench
