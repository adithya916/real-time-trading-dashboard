import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeVenue

from ingest.adapters.base import AdapterError
from ingest.adapters.binance import BinanceAdapter
from ingest.recording import load_frames, load_rest
from mdcommon.schema import Aggressor, Envelope, ResetReason

FIXTURE = Path(__file__).parent / "fixtures" / "binance_btcusdt.jsonl.gz"
FRAMES = load_frames(FIXTURE)  # 20 s of live Binance BTCUSDT, recorded with --record
SNAPSHOT = load_rest(FIXTURE)[0]
DEPTH = [json.loads(f)["data"] for f in FRAMES if '"depthUpdate"' in f]


class FakeRest:
    """Returns the scripted snapshot bodies in order, repeating the last one."""

    def __init__(self, *bodies: dict[str, Any]) -> None:
        self.bodies = list(bodies)
        self.calls: list[str] = []

    async def __call__(self, symbol: str) -> dict[str, Any]:
        self.calls.append(symbol)
        return self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]


def replay(
    frames: list[str], rest: FakeRest
) -> tuple[BinanceAdapter, list[Envelope], BaseException | None]:
    venue = FakeVenue([frames])
    adapter = BinanceAdapter(["BTC-USDT"], connect=venue.connect, fetch=rest)

    async def drain() -> tuple[list[Envelope], BaseException | None]:
        out: list[Envelope] = []
        try:
            async for envelope in adapter.stream():
                out.append(envelope)
        except Exception as exc:
            return out, exc
        return out, None

    envelopes, error = asyncio.run(drain())
    return adapter, envelopes, error


def kinds(envelopes: list[Envelope]) -> list[str]:
    return [str(e.WhichOneof("payload")) for e in envelopes]


def without(frames: list[str], event: dict[str, Any]) -> list[str]:
    marker = f'"U":{event["U"]},'
    return [f for f in frames if marker not in f]


def test_recording_syncs_once_and_applies_every_newer_event() -> None:
    adapter, envelopes, error = replay(FRAMES, FakeRest(SNAPSHOT))
    stale = sum(1 for e in DEPTH if e["u"] <= SNAPSHOT["lastUpdateId"])

    assert isinstance(error, OSError)  # the recording ends like a closed socket
    assert kinds(envelopes).count("snapshot") == 1
    assert "reset" not in kinds(envelopes)
    assert stale > 0  # the snapshot really was newer than the first events
    assert kinds(envelopes).count("delta") == len(DEPTH) - stale
    assert adapter.states["BTC-USDT"].last_u == DEPTH[-1]["u"]
    assert not adapter.states["BTC-USDT"].book.is_crossed()


def test_seq_is_gapless_and_trades_come_through() -> None:
    _, envelopes, _ = replay(FRAMES, FakeRest(SNAPSHOT))

    assert [e.seq for e in envelopes] == list(range(1, len(envelopes) + 1))
    assert kinds(envelopes).count("trade") > 100
    snapshot = next(e.snapshot for e in envelopes if e.HasField("snapshot"))
    assert (snapshot.price_scale, snapshot.size_scale) == (2, 5)
    assert len(snapshot.bids) == len(snapshot.asks) == 1000


def test_a_missing_event_resets_and_resyncs_on_the_same_connection() -> None:
    applied = [e for e in DEPTH if e["u"] > SNAPSHOT["lastUpdateId"]]
    lost, after = applied[3], applied[4]
    # The resync snapshot sits just before the event after the hole, as a fresh one would.
    resync = {**SNAPSHOT, "lastUpdateId": after["U"] - 1}
    rest = FakeRest(SNAPSHOT, resync)

    _, envelopes, _ = replay(without(FRAMES, lost), rest)
    reset = next(e.reset for e in envelopes if e.HasField("reset"))

    assert reset.reason == ResetReason.RESET_REASON_SEQUENCE_GAP
    assert (reset.expected_seq, reset.received_seq) == (lost["U"], after["U"])
    assert kinds(envelopes).count("snapshot") == 2
    assert rest.calls == ["BTCUSDT", "BTCUSDT"]
    assert kinds(envelopes)[-1] in {"delta", "trade"}


def test_a_stale_snapshot_is_fetched_again() -> None:
    stale = {**SNAPSHOT, "lastUpdateId": DEPTH[0]["U"] - 10}
    rest = FakeRest(stale, SNAPSHOT)

    _, envelopes, _ = replay(FRAMES, rest)

    assert len(rest.calls) == 2
    assert kinds(envelopes).count("snapshot") == 1
    assert "reset" not in kinds(envelopes)


def test_giving_up_on_snapshots_is_an_error() -> None:
    stale = {**SNAPSHOT, "lastUpdateId": DEPTH[0]["U"] - 10}
    _, _, error = replay(FRAMES, FakeRest(stale))

    assert isinstance(error, AdapterError)
    assert "no snapshot newer" in str(error)


@pytest.mark.parametrize(("buyer_is_maker", "aggressor"), [(True, "SELL"), (False, "BUY")])
def test_trade_aggressor_follows_the_maker_flag(buyer_is_maker: bool, aggressor: str) -> None:
    trade = {
        "e": "trade",
        "s": "BTCUSDT",
        "t": 7,
        "p": "67212.41000000",
        "q": "0.00100000",
        "T": 1_731_000_000_000,
        "m": buyer_is_maker,
    }
    frame = json.dumps({"stream": "btcusdt@trade", "data": trade})
    _, envelopes, _ = replay([frame], FakeRest(SNAPSHOT))

    [envelope] = envelopes
    assert envelope.trade.aggressor == getattr(Aggressor, f"AGGRESSOR_{aggressor}")
    assert (envelope.trade.price_ticks, envelope.trade.size_lots) == (6721241, 100)
    assert envelope.ts_exch_ns == 1_731_000_000_000_000_000


def test_url_names_a_depth_and_a_trade_stream_per_symbol() -> None:
    adapter = BinanceAdapter(["BTC-USDT", "ETH-USDT"])
    assert adapter.url() == (
        "wss://stream.binance.com:9443/stream?streams="
        "btcusdt@depth@100ms/btcusdt@trade/ethusdt@depth@100ms/ethusdt@trade"
    )


def test_unknown_symbols_are_rejected_up_front() -> None:
    with pytest.raises(ValueError, match="no Binance instrument"):
        BinanceAdapter(["DOGE-EUR"])
