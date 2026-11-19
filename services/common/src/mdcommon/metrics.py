"""Prometheus metrics every service shares, so dashboards and alerts use one set of names.

md_envelopes_total{service, venue, kind}      envelopes published or consumed
md_book_resets_total{service, venue, reason}  books thrown away (checksum, gap, reconnect)
md_gateway_viewers{venue, symbol}             open WebSocket viewers
md_gateway_frames_total{kind}                 frames queued to viewers
md_gateway_resyncs_total                      mirror resyncs from the snapshot bucket
md_gateway_slow_disconnects_total             viewers closed for falling behind (1013)
md_trades_stored_total                        trades written to TimescaleDB
md_paper_fills_total{side}                    the paper trader's fills
md_latency_ms{hop, quantile}                  per-hop latency (mdcommon.latency)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

ENVELOPES = Counter("md_envelopes", "Envelopes published or consumed", ["service", "venue", "kind"])
RESETS = Counter("md_book_resets", "Books thrown away", ["service", "venue", "reason"])
VIEWERS = Gauge("md_gateway_viewers", "Open WebSocket viewers", ["venue", "symbol"])
FRAMES = Counter("md_gateway_frames", "Frames queued to viewers", ["kind"])
RESYNCS = Counter("md_gateway_resyncs", "Mirror resyncs from the snapshot bucket")
SLOW_DISCONNECTS = Counter("md_gateway_slow_disconnects", "Viewers closed for falling behind")
TRADES_STORED = Counter("md_trades_stored", "Trades written to TimescaleDB")
PAPER_FILLS = Counter("md_paper_fills", "Paper trader fills", ["side"])
