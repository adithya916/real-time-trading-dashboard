# 006: One free ARM server, everything behind Caddy

## Context

The project should be visible at a public link at no cost, around the clock, with live feeds,
the bus, a database and the dashboards. Free tiers that sleep, cap requests per month or expire
after a trial don't fit a system that streams all day.

## Decision

- **Oracle Cloud Always Free ARM VM** (Ampere A1, up to the free allowance of cores and memory)
  running the Compose stack. It is the free option that stays on and has the memory for NATS,
  TimescaleDB, Prometheus and Grafana together.
- **Caddy** in front, the only thing published (80/443): it serves the built dashboard, proxies
  the gateway's WebSocket and API and Grafana under `/grafana`, and gets and renews its own
  certificate. A free `<ip>.sslip.io` name works without buying a domain.
- **Build on the server.** The deploy workflow SSHes in (host key pinned), checks out the
  pushed commit and rebuilds with Docker's cache. The ARM server compiles the C++ core natively.

## Alternatives

- **Render / Koyeb / Fly free tiers**: sleep when idle, or are trials; a WebSocket feed must
  stay up.
- **Upstash and similar serverless data stores**: per-request caps far below a tick stream.
- **Cloudflare Pages for the dashboard**: free and fast, but a second account and a
  cross-origin WebSocket; one host keeps it simple.
- **Prebuilt multi-arch images on GHCR**: faster deploys, but C++ under QEMU emulation is slow
  to build in CI, and a registry is one more thing to manage.

## Consequences

- One machine is a single point of failure and the free allowance caps CPU; the load test
  (`docs/PERFORMANCE.md`) measures what one gateway process serves.
- Oracle requires a card to verify identity and its ARM capacity is sometimes unavailable;
  `docs/DEPLOY.md` covers both. The same Compose files run on any Ubuntu server.
- The production setup is tested in CI behind Caddy with a local certificate authority.
