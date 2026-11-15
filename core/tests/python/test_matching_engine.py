from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from bookcore import EventType, MatchingEngine, OrderType, RejectReason, Side


def test_a_crossing_order_trades_at_the_resting_price() -> None:
    engine = MatchingEngine()
    engine.submit(1, Side.ASK, OrderType.LIMIT, 101, 3)
    events = engine.submit(2, Side.BID, OrderType.LIMIT, 105, 5)

    [trade] = [e for e in events if e.type == EventType.TRADE]
    assert (trade.order, trade.maker, trade.price, trade.size) == (2, 1, 101, 3)
    assert engine.best(Side.BID) == (105, 2)
    assert [e.seq for e in events] == sorted(e.seq for e in events)


def test_rejections_name_the_reason() -> None:
    engine = MatchingEngine()
    [event] = engine.cancel(7)
    assert event.type == EventType.REJECTED
    assert event.reason == RejectReason.UNKNOWN_ORDER


class EngineMachine(RuleBasedStateMachine):
    """Random submits and cancels. The book must never cross, every lot must be accounted
    for, and the L2 view must add up to what is resting."""

    def __init__(self) -> None:
        super().__init__()
        self.engine = MatchingEngine()
        self.next_id = 1
        self.submitted = 0
        self.traded = 0
        self.cancelled = 0

    def _record(self, events: list) -> None:  # type: ignore[type-arg]
        for event in events:
            if event.type == EventType.TRADE:
                self.traded += event.size
            elif event.type == EventType.CANCELLED:
                self.cancelled += event.size

    @rule(
        side=st.sampled_from([Side.BID, Side.ASK]),
        order_type=st.sampled_from([OrderType.LIMIT] * 4 + [OrderType.MARKET, OrderType.IOC]),
        price=st.integers(min_value=95, max_value=105),
        size=st.integers(min_value=1, max_value=20),
    )
    def submit(self, side: Side, order_type: OrderType, price: int, size: int) -> None:
        self.submitted += size
        self._record(self.engine.submit(self.next_id, side, order_type, price, size))
        self.next_id += 1

    @rule(data=st.data())
    def cancel(self, data: st.DataObject) -> None:
        order = data.draw(st.integers(min_value=1, max_value=max(1, self.next_id)))
        self._record(self.engine.cancel(order))

    @invariant()
    def never_crossed(self) -> None:
        bid, ask = self.engine.best(Side.BID), self.engine.best(Side.ASK)
        assert not (bid and ask and bid[0] >= ask[0])

    @invariant()
    def every_lot_is_accounted_for(self) -> None:
        resting = sum(
            size for side in (Side.BID, Side.ASK) for _, size in self.engine.depth(side, 1000)
        )
        assert self.submitted == 2 * self.traded + resting + self.cancelled

    @invariant()
    def levels_are_sorted_best_first(self) -> None:
        bids = [p for p, _ in self.engine.depth(Side.BID, 1000)]
        asks = [p for p, _ in self.engine.depth(Side.ASK, 1000)]
        assert bids == sorted(bids, reverse=True)
        assert asks == sorted(asks)


EngineMachine.TestCase.settings = settings(max_examples=200, stateful_step_count=60, deadline=None)
TestEngineMachine = EngineMachine.TestCase
