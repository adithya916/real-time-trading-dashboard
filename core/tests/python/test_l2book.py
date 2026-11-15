import pytest

from bookcore import L2Book, Side, kraken_checksum


def test_levels_cross_the_binding_as_tuples_best_first() -> None:
    book = L2Book()
    for price, size in [(98, 1), (100, 2), (99, 3)]:
        book.apply(Side.BID, price, size)
    book.apply(Side.ASK, 101, 4)

    assert book.top(Side.BID, 10) == [(100, 2), (99, 3), (98, 1)]
    assert book.best(Side.ASK) == (101, 4)
    assert book.best(Side.BID) == (100, 2)
    assert len(book) == 4
    assert book.depth(Side.BID) == 3


def test_empty_side_has_no_best() -> None:
    assert L2Book().best(Side.ASK) is None


def test_negative_size_raises_value_error() -> None:
    with pytest.raises(ValueError, match="negative size"):
        L2Book().apply(Side.BID, 100, -1)


def test_checksum_matches_the_cpp_suite() -> None:
    book = L2Book()
    book.apply(Side.BID, 100, 5)
    book.apply(Side.BID, 99, 7)
    book.apply(Side.ASK, 101, 2)
    book.apply(Side.ASK, 102, 9)
    assert kraken_checksum(book) == 3251632201


def test_large_prices_survive_the_round_trip() -> None:
    book = L2Book()
    book.apply(Side.ASK, 2**62, 2**40)
    assert book.best(Side.ASK) == (2**62, 2**40)
