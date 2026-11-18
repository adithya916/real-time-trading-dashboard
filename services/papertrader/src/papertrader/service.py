"""Run the paper trader against the simulated venue over NATS.

    python -m papertrader.service --bus nats://localhost:4222 BTC-SIM [--account mm]

Market data: md.sim.<symbol>.* on one subscription (book and trade envelopes share a gapless
seq), starting from the snapshot bucket; a gap reloads the snapshot. Fills:
fills.sim.<symbol>.<account>. Orders: request/reply on orders.sim.<symbol>. Every 250 ms the
trader decides; every 500 ms it publishes its StrategyState to md.sim.<symbol>.strategy,
which the gateway forwards to the dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Any

from mdcommon.schema import Envelope
from papertrader.trader import PaperTrader

log = logging.getLogger(__name__)
VENUE = "sim"
DECIDE_EVERY_S = 0.25
STATE_EVERY_S = 0.5


async def run(bus_url: str, symbol: str, account: str) -> None:
    import nats

    from mdcommon.bus import SNAPSHOT_BUCKET, ensure_stream, snapshot_key, subject_for

    nc = await nats.connect(bus_url, name="papertrader", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await ensure_stream(js)
    kv = await js.key_value(SNAPSHOT_BUCKET)
    trader = PaperTrader(symbol, account)

    async def resync() -> None:
        try:
            entry = await kv.get(snapshot_key(VENUE, symbol))
            if entry.value:
                trader.load_snapshot(Envelope.FromString(entry.value))
        except Exception as exc:  # no snapshot yet; the next decide() waits for one
            log.debug("resync: %r", exc)

    async def on_market(msg: Any) -> None:
        if trader.on_envelope(Envelope.FromString(msg.data)):
            await resync()

    async def on_fill(msg: Any) -> None:
        trader.on_fill(json.loads(msg.data))

    await nc.subscribe(f"md.{VENUE}.{symbol}.*", cb=on_market)
    await nc.subscribe(f"fills.{VENUE}.{symbol}.{account}", cb=on_fill)
    await resync()

    async def send(requests: list[dict[str, Any]]) -> None:
        for request in requests:
            try:
                msg = await nc.request(
                    f"orders.{VENUE}.{symbol}", json.dumps(request).encode(), timeout=2
                )
                trader.on_reply(request, json.loads(msg.data))
            except Exception as exc:  # the venue is away; the order state heals next round
                log.warning("order request failed (%r)", exc)
                trader.on_reply(request, {"ok": False})

    seq, last_state = 0, 0.0
    log.info("papertrader: quoting %s on %s as %s", symbol, VENUE, account)
    try:
        while True:
            await asyncio.sleep(DECIDE_EVERY_S)
            if not trader.synced:
                await resync()
            await send(trader.decide())
            if time.monotonic() - last_state >= STATE_EVERY_S and trader.synced:
                seq += 1
                envelope = trader.state(VENUE, seq, time.time_ns())
                await nc.publish(subject_for(envelope), envelope.SerializeToString())
                last_state = time.monotonic()
    finally:
        await send(trader.cancel_all())
        await nc.drain()


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-trade a market-maker on the simulator.")
    parser.add_argument("symbol")
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--account", default="mm")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.bus, args.symbol, args.account))


if __name__ == "__main__":
    main()
