# Architecture

Live order books from real venues and a simulated one, normalized into one schema, carried
over a bus, enriched, stored, and served to browsers through a conflating gateway. Decisions
and their alternatives are in [`adr/`](adr/); measured numbers are in
[`PERFORMANCE.md`](PERFORMANCE.md).

## The pipeline

```mermaid
flowchart LR
  subgraph Sources
    KR["Kraken v2 WS<br/>CRC32 checksum"]
    BN["Binance WS<br/>REST snapshot + U/u ids"]
    SIM["simulator<br/>ZI agents → C++ engine"]
    REC[("Parquet<br/>recordings")]
  end
  KR & BN --> ING["ingest<br/>adapters · PyBook<br/>gapless seq"]
  REC -- "recorder.replay<br/>any speed" --> BUS
  ING --> BUS[("NATS JetStream<br/>md.&lt;venue&gt;.&lt;symbol&gt;.&lt;kind&gt;<br/>KV: latest snapshots")]
  SIM --> BUS
  BUS --> AN["analytics<br/>microprice · imbalance<br/>vol · VWAP"]
  AN -- metrics --> BUS
  AN -- "trades (COPY)" --> TS[("TimescaleDB<br/>1m/5m/1h candles")]
  BUS --> RECD["recorder"] --> REC
  BUS --> PT["papertrader<br/>imbalance market-maker"]
  PT -- "orders (request/reply)" --> SIM
  SIM -- fills --> PT
  PT -- StrategyState --> BUS
  BUS --> GW["gateway<br/>mirror per book<br/>50 ms conflation"]
  TS --> GW
  GW -- "wss: one message per tick" --> WEB["browser<br/>worker decodes<br/>canvas charts"]
```

| Service | Role | Code |
| --- | --- | --- |
| ingest | One adapter per venue: connect, subscribe, keep a reference book, verify (Kraken CRC32, Binance update ids), publish snapshots, deltas, trades and resets with a gapless seq | `services/ingest` |
| simulator | A venue of its own: zero-intelligence agents around a random-walk fair value, matched by the C++ engine; takes outside orders | `services/simulator`, `core/` |
| analytics | Mirrors every book; publishes microstructure metrics; stores trades | `services/analytics` |
| recorder | Writes every envelope to hourly Parquet; replays them | `services/recorder` |
| papertrader | Market-makes the simulated venue with risk limits; publishes its PnL | `services/papertrader` |
| gateway | One bus subscription, a mirror per book, conflated fan-out, candle history | `services/gateway` |
| web | Worker decodes and keeps state; panels read one view per animation frame | `web/` |

## Keeping books correct

Every consumer of a book follows the same protocol as a venue's own feed.

```mermaid
stateDiagram-v2
  [*] --> Waiting
  Waiting --> Synced: snapshot (stream or KV bucket)
  Synced --> Synced: next seq applies
  Synced --> Waiting: seq gap, or BookReset
  Waiting --> Synced: KV snapshot + buffered envelopes after its seq
```

- Ingest assigns a gapless `seq` per venue and symbol to every book and trade envelope. A venue
  checksum mismatch or sequence gap makes the adapter emit `BookReset` and resubscribe.
- About once a second ingest writes each in-sync book to the `snap` KV bucket, tagged with the
  seq of the last envelope it includes. A consumer that joins late or sees a gap loads it and
  applies the buffered envelopes after that seq.
- Consumers read book and trade envelopes on **one** subscription (`md.*.*.*`): NATS runs each
  subscription's callbacks independently, so two subscriptions would reorder them.
- Golden replay hashes (`ingest/tests/golden/`) pin the books each recording must produce,
  through ingest, through Parquet and through the bus.

## Fan-out to browsers

```mermaid
sequenceDiagram
  participant B as NATS
  participant M as gateway mirror
  participant T as 50 ms tick
  participant V as viewers (n)
  B->>M: deltas, trades, metrics (any rate)
  Note over M: apply to the book,<br/>collect changed levels
  T->>M: drain
  M->>T: one coalesced delta + trades + latest metrics
  Note over T: build the message once:<br/>size-delimited envelopes
  T->>V: same bytes to every in-sync viewer<br/>(snapshot version to new or resynced ones)
  V-->>T: ping text, echoed after queued data
```

- At most one message per viewer per tick (20/s) whatever the feed rate; trades are never
  dropped while a viewer keeps up.
- A viewer whose queue (100 messages, about 5 s) fills loses its backlog and gets a fresh
  snapshot; one still behind 5 s later is closed with code 1013.

## Observability

Every hop is timed from the envelope's own timestamps into HdrHistograms (`mdcommon.latency`)
and exported on `/metrics` with counters for envelopes, resets, viewers, frames and resyncs.
Prometheus scrapes them and Grafana shows the **Market data pipeline** dashboard, both
provisioned from `deploy/`. Alerts: checksum failures, gateway lag, a service down.

## Deployment

```mermaid
flowchart LR
  U["visitors"] -- "https / wss" --> C["Caddy :443<br/>TLS, static dashboard"]
  C -- "/ws /symbols /history" --> GW["gateway"]
  C -- "/grafana" --> GR["grafana"]
  subgraph VM["one ARM server, Docker Compose"]
    C
    GW
    GR
    N[("NATS")]
    DB[("TimescaleDB")]
    P["prometheus"]
    S["ingest · simulator · analytics · papertrader"]
  end
  GH["GitHub Actions"] -- "ssh: fetch, rebuild, restart" --> VM
```

Only Caddy is published. See [`DEPLOY.md`](DEPLOY.md).
