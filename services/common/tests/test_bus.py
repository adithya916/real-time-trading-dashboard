import pytest

from mdcommon.bus import snapshot_key, subject, subject_for
from mdcommon.schema import Analytics, BookReset, Delta, Envelope, Snapshot, Trade


def envelope_with(field: str) -> Envelope:
    envelope = Envelope(venue="kraken", symbol="BTC-USD", seq=1)
    payloads = {
        "snapshot": Snapshot(),
        "delta": Delta(),
        "reset": BookReset(),
        "trade": Trade(),
        "analytics": Analytics(),
    }
    getattr(envelope, field).CopyFrom(payloads[field])
    return envelope


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("snapshot", "md.kraken.BTC-USD.book"),
        ("delta", "md.kraken.BTC-USD.book"),
        ("reset", "md.kraken.BTC-USD.book"),
        ("trade", "md.kraken.BTC-USD.trade"),
        ("analytics", "md.kraken.BTC-USD.analytics"),
    ],
)
def test_book_events_share_one_subject_so_they_stay_in_order(payload: str, expected: str) -> None:
    assert subject_for(envelope_with(payload)) == expected


@pytest.mark.parametrize("bad", ["BTC.USD", "BTC*", "BTC>", "BTC USD", ""])
def test_tokens_that_would_break_subjects_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="subject token"):
        subject("kraken", bad, "book")


def test_unknown_kinds_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown subject kind"):
        subject("kraken", "BTC-USD", "quotes")


def test_an_empty_envelope_has_no_subject() -> None:
    with pytest.raises(ValueError, match="no payload"):
        subject_for(Envelope(venue="kraken", symbol="BTC-USD"))


def test_snapshot_keys_are_venue_dot_symbol() -> None:
    assert snapshot_key("binance", "BTC-USDT") == "binance.BTC-USDT"
