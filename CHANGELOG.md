# Changelog

## Unreleased

- Monorepo layout: `services/legacy` (FastAPI backend), `web` (React dashboard), `deploy/compose` (one Compose file per service).
- Repo conventions: LF line endings, EditorConfig, pre-commit hooks (ruff, Prettier), Makefile.
- CI: `ci-python` (ruff, mypy, pytest), `ci-web` (ESLint, Prettier, build) and `integration` (Compose up, health and WebSocket smoke test), each with path filters and one always-reported `*-ok` check for branch protection.
- Legacy backend test suite (12 tests) and mypy fixes.
