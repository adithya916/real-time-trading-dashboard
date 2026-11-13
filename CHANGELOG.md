# Changelog

## Unreleased

- Monorepo layout: `services/legacy` (FastAPI backend), `web` (React dashboard), `deploy/compose` (one Compose file per service).
- Repo conventions: LF line endings, EditorConfig, pre-commit hooks (ruff, Prettier), Makefile.
- CI: `ci-python` (ruff, mypy, pytest), `ci-web` (ESLint, Prettier, build) and `integration` (Compose up, health and WebSocket smoke test), each with path filters and one always-reported `*-ok` check for branch protection.
- Legacy backend test suite (12 tests) and mypy fixes.
- Protobuf schema `marketdata.v1` (Envelope with per-hop timestamps; Snapshot, Delta, Trade, BookReset, Analytics; integer prices), linted with buf in CI.
- `services/common` (`mdcommon`): generated schema code plus string-exact tick conversion. Generated code is built by `scripts/gen_proto.sh`, never committed. TypeScript generation follows on Day 5 with the web worker.
- Baseline benchmark (`bench/baseline/run.py`, `bench-baseline` workflow): pipeline throughput and latency, one viewer's message rate, and a viewer ramp until p99 passes 100 ms. Results in `bench/results/baseline.md`, tagged `v0-baseline`.
