import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from ingest.book.pybook import BookSide, PyBook


def test_insert_update_and_delete_levels() -> None:
    book = PyBook()
    book.apply("bid", 100, 5)
    book.apply("bid", 101, 7)
    book.apply("ask", 103, 2)
    book.apply("bid", 100, 9)
    book.apply("bid", 101, 0)

    assert book.top("bid") == [(100, 9)]
    assert book.best_ask() == (103, 2)
    assert len(book) == 2


def test_removing_a_missing_level_is_harmless() -> None:
    book = PyBook()
    book.apply("ask", 105, 0)
    assert len(book) == 0


def test_top_orders_each_side_best_first() -> None:
    book = PyBook()
    for price in (98, 100, 99):
        book.apply("bid", price, 1)
    for price in (103, 101, 102):
        book.apply("ask", price, 1)

    assert [p for p, _ in book.top("bid")] == [100, 99, 98]
    assert [p for p, _ in book.top("ask", 2)] == [101, 102]


def test_truncate_keeps_the_best_levels() -> None:
    book = PyBook()
    for price in range(90, 100):
        book.apply("bid", price, 1)
    for price in range(101, 111):
        book.apply("ask", price, 1)
    book.truncate(3)

    assert [p for p, _ in book.top("bid")] == [99, 98, 97]
    assert [p for p, _ in book.top("ask")] == [101, 102, 103]


def test_crossing_is_detected() -> None:
    book = PyBook()
    book.apply("bid", 100, 1)
    book.apply("ask", 101, 1)
    assert not book.is_crossed()
    book.apply("bid", 101, 1)
    assert book.is_crossed()


def test_negative_sizes_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative size"):
        PyBook().apply("bid", 100, -1)


bid_prices = st.integers(min_value=1, max_value=100)
ask_prices = st.integers(min_value=101, max_value=200)
sizes = st.integers(min_value=0, max_value=1_000)
SIDES: tuple[BookSide, BookSide] = ("bid", "ask")


class BookMachine(RuleBasedStateMachine):
    """Random update streams checked against a plain dict model.

    Bids stay at or below 100 and asks above it, so the book must never cross
    unless the crossing rule deliberately makes it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.book = PyBook()
        self.model: dict[BookSide, dict[int, int]] = {"bid": {}, "ask": {}}
        self.crossing_price: int | None = None

    def _apply(self, side: BookSide, price: int, size: int) -> None:
        self.book.apply(side, price, size)
        if size:
            self.model[side][price] = size
        else:
            self.model[side].pop(price, None)

    @rule(price=bid_prices, size=sizes)
    def update_bid(self, price: int, size: int) -> None:
        self._apply("bid", price, size)

    @rule(price=ask_prices, size=sizes)
    def update_ask(self, price: int, size: int) -> None:
        self._apply("ask", price, size)

    @precondition(lambda self: self.crossing_price is None and self.book.best_ask() is not None)
    @rule()
    def cross_the_book(self) -> None:
        best_ask = self.book.best_ask()
        assert best_ask is not None
        self.crossing_price = best_ask[0]
        self.book.apply("bid", self.crossing_price, 1)

    @precondition(lambda self: self.crossing_price is not None)
    @rule()
    def uncross(self) -> None:
        assert self.crossing_price is not None
        self.book.apply("bid", self.crossing_price, 0)
        self.crossing_price = None

    @precondition(lambda self: self.crossing_price is None)
    @rule(depth=st.integers(min_value=1, max_value=20))
    def truncate(self, depth: int) -> None:
        self.book.truncate(depth)
        for side in SIDES:
            kept = sorted(self.model[side].items(), reverse=side == "bid")[:depth]
            self.model[side] = dict(kept)

    @invariant()
    def matches_the_model(self) -> None:
        bids = dict(self.book.bids)
        if self.crossing_price is not None:
            bids.pop(self.crossing_price, None)
        assert bids == self.model["bid"]
        assert self.book.asks == self.model["ask"]

    @invariant()
    def sides_are_sorted_with_positive_sizes(self) -> None:
        for side in SIDES:
            levels = self.book.top(side)
            prices = [p for p, _ in levels]
            assert prices == sorted(prices, reverse=side == "bid")
            assert all(size > 0 for _, size in levels)

    @invariant()
    def crosses_only_when_told_to(self) -> None:
        # Crossed exactly when the deliberate bid still reaches the model's best ask;
        # removing that ask uncrosses the book even though the bid is still there.
        asks = self.model["ask"]
        expected = (
            self.crossing_price is not None and bool(asks) and self.crossing_price >= min(asks)
        )
        assert self.book.is_crossed() == expected


TestBookMachine = BookMachine.TestCase
