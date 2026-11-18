from hypothesis import given
from hypothesis import strategies as st

from ingest.book.pybook import PyBook
from mdcommon.schema import Delta, Envelope, Level, LevelUpdate, Side, Snapshot
from papertrader.pnl import Position, to_money
from papertrader.strategy import Params, imbalance, quotes
from papertrader.trader import PaperTrader


def book(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> PyBook:
    b = PyBook()
    for price, size in bids:
        b.apply("bid", price, size)
    for price, size in asks:
        b.apply("ask", price, size)
    return b


# PnL ------------------------------------------------------------------------------------------


def test_round_trip_realizes_the_difference() -> None:
    p = Position()
    p.fill("bid", 100, 10)
    p.fill("bid", 110, 10)
    assert (p.lots, p.avg_ticks) == (20, 105)
    p.fill("ask", 120, 5)
    assert p.realized == 5 * 15 and p.lots == 15
    assert p.unrealized(100) == 15 * -5


def test_a_fill_through_flat_opens_the_other_side_at_its_price() -> None:
    p = Position()
    p.fill("ask", 200, 3)
    p.fill("bid", 190, 5)
    assert p.realized == 3 * 10 and (p.lots, p.avg_ticks) == (2, 190)


fills = st.lists(
    st.tuples(st.sampled_from(["bid", "ask"]), st.integers(1, 1000), st.integers(1, 50)),
    max_size=60,
)


@given(fills, st.integers(1, 1000))
def test_pnl_equals_cash_plus_marked_position(
    trades: list[tuple[str, int, int]], mark: int
) -> None:
    p = Position()
    cash = 0
    for side, price, size in trades:
        p.fill(side, price, size)  # type: ignore[arg-type]
        cash += -price * size if side == "bid" else price * size
    equity = cash + p.lots * mark
    assert abs((p.realized + p.unrealized(mark)) - equity) < 1e-6 * max(1, abs(equity))


def test_money_uses_both_scales() -> None:
    assert to_money(500_000 * 10_000, price_scale=1, size_scale=4) == 50_000.0


# Quotes ---------------------------------------------------------------------------------------


def test_quotes_straddle_the_microprice_and_never_cross() -> None:
    b = book([(1000, 50)], [(1010, 50)])
    q = quotes(b, 0, Params(half_spread_ticks=3, imbalance_ticks=0))
    assert q.bid is not None and q.ask is not None
    assert q.bid < 1005 < q.ask
    tight = quotes(b, 0, Params(half_spread_ticks=-50, imbalance_ticks=0))
    assert tight.bid is not None and tight.ask is not None
    assert tight.bid < 1010 and tight.ask > 1000  # post-only on both sides


def test_more_bids_lean_the_quotes_up_and_long_inventory_leans_them_down() -> None:
    balanced = quotes(book([(1000, 50)], [(1010, 50)]), 0, Params())
    bid_heavy = quotes(book([(1000, 500)], [(1010, 50)]), 0, Params())
    long = quotes(book([(1000, 50)], [(1010, 50)]), 100, Params())
    assert balanced.bid is not None and bid_heavy.bid is not None and long.bid is not None
    assert bid_heavy.bid > balanced.bid
    assert long.bid < balanced.bid
    assert imbalance(book([(1, 3)], [(2, 1)])) == 0.5


def test_position_limits_stop_the_side_that_would_add_risk() -> None:
    b = book([(1000, 50)], [(1010, 50)])
    params = Params(max_position_lots=100)
    assert quotes(b, 100, params).bid is None and quotes(b, 100, params).ask is not None
    assert quotes(b, -100, params).ask is None and quotes(b, -100, params).bid is not None


# The trader -----------------------------------------------------------------------------------


def snapshot(seq: int) -> Envelope:
    e = Envelope(venue="sim", symbol="BTC-SIM", seq=seq)
    e.snapshot.CopyFrom(
        Snapshot(
            bids=[Level(price_ticks=1000, size_lots=50)],
            asks=[Level(price_ticks=1020, size_lots=50)],
            price_scale=1,
            size_scale=4,
        )
    )
    return e


def test_the_trader_quotes_requotes_and_counts_fills() -> None:
    t = PaperTrader("BTC-SIM", params=Params(half_spread_ticks=2, imbalance_ticks=0))
    assert t.decide() == []  # no book yet
    t.on_envelope(snapshot(1))
    first = t.decide()
    assert [(r["op"], r["side"]) for r in first] == [("new", "bid"), ("new", "ask")]
    for r in first:
        t.on_reply(r, {"ok": True, "resting": r["size"]})
    assert t.decide() == []  # nothing moved
    bid = t.live["bid"]
    assert bid is not None
    t.on_fill({"id": bid.id, "side": "bid", "price": bid.price, "size": 5, "taker": False})
    live = t.live["bid"]
    assert t.position.lots == 5 and live is not None and live.remaining == 15
    # The book moves up: both quotes are replaced, cancels first.
    move = Envelope(venue="sim", symbol="BTC-SIM", seq=2)
    move.delta.CopyFrom(
        Delta(
            updates=[
                LevelUpdate(side=Side.SIDE_BID, price_ticks=1100, size_lots=50),
                LevelUpdate(side=Side.SIDE_ASK, price_ticks=1120, size_lots=50),
                LevelUpdate(side=Side.SIDE_BID, price_ticks=1000, size_lots=0),
                LevelUpdate(side=Side.SIDE_ASK, price_ticks=1020, size_lots=0),
            ]
        )
    )
    assert not t.on_envelope(move)
    ops = [(r["op"], r.get("side")) for r in t.decide()]
    assert ops == [("cancel", None), ("new", "bid"), ("cancel", None), ("new", "ask")]


def test_a_seq_gap_stops_quoting_until_a_snapshot_arrives() -> None:
    t = PaperTrader("BTC-SIM")
    t.on_envelope(snapshot(1))
    gap = Envelope(venue="sim", symbol="BTC-SIM", seq=5)
    gap.delta.CopyFrom(Delta())
    assert t.on_envelope(gap) is True
    assert t.decide() == []
    t.load_snapshot(snapshot(9))
    assert t.decide()


def test_the_loss_limit_halts_and_pulls_every_quote() -> None:
    t = PaperTrader("BTC-SIM", params=Params(max_loss=1.0, imbalance_ticks=0))
    t.on_envelope(snapshot(1))
    for r in t.decide():
        t.on_reply(r, {"ok": True, "resting": r["size"]})
    t.on_fill({"id": 99, "side": "bid", "price": 5000, "size": 100, "taker": True})  # bought high
    requests = t.decide()
    assert t.halted and "loss limit" in t.halt_reason
    assert [r["op"] for r in requests] == ["cancel", "cancel"]
    assert t.decide() == []
    state = t.state("sim", 3, 0).strategy
    assert state.halted and state.position_lots == 100 and state.bid_price_ticks == 0
    assert state.unrealized_pnl < 0


def test_a_rejected_order_is_forgotten() -> None:
    t = PaperTrader("BTC-SIM")
    t.on_envelope(snapshot(1))
    requests = t.decide()
    t.on_reply(requests[0], {"ok": False, "error": "rejected"})
    assert t.live["bid"] is None and t.rejected == 1
