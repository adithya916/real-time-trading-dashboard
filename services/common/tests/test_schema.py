import pytest

from mdcommon.schema import (
    Aggressor,
    Analytics,
    BookReset,
    Delta,
    Envelope,
    Level,
    LevelUpdate,
    ResetReason,
    Side,
    Snapshot,
    Trade,
    from_ticks,
    to_ticks,
)


def envelope(**payload: object) -> Envelope:
    return Envelope(
        venue="kraken",
        symbol="BTC-USD",
        seq=1042,
        ts_exch_ns=1_700_000_000_000_000_000,
        ts_recv_ns=1_700_000_000_000_100_000,
        **payload,  # type: ignore[arg-type]
    )


PAYLOADS = {
    "snapshot": Snapshot(
        price_scale=1,
        size_scale=8,
        bids=[Level(price_ticks=674120, size_lots=84_210_000)],
        asks=[Level(price_ticks=674125, size_lots=12_000_000)],
        checksum=3310070434,
    ),
    "delta": Delta(
        updates=[LevelUpdate(side=Side.SIDE_BID, price_ticks=674115, size_lots=0)],
        checksum=1234,
    ),
    "trade": Trade(
        trade_id="t-1", price_ticks=674125, size_lots=500_000, aggressor=Aggressor.AGGRESSOR_BUY
    ),
    "reset": BookReset(
        reason=ResetReason.RESET_REASON_SEQUENCE_GAP, expected_seq=1043, received_seq=1044
    ),
    "analytics": Analytics(mid=67412.25, microprice=67412.4, spread_bps=0.07, imbalance_l1=0.75),
}


@pytest.mark.parametrize("kind", PAYLOADS)
def test_every_payload_round_trips(kind: str) -> None:
    original = envelope(**{kind: PAYLOADS[kind]})
    decoded = Envelope.FromString(original.SerializeToString())

    assert decoded == original
    assert decoded.WhichOneof("payload") == kind


def test_setting_a_payload_clears_the_previous_one() -> None:
    message = envelope(trade=PAYLOADS["trade"])
    message.reset.reason = ResetReason.RESET_REASON_RECONNECT
    assert message.WhichOneof("payload") == "reset"
    assert not message.HasField("trade")


@pytest.mark.parametrize(
    ("text", "scale", "ticks"),
    [
        ("67412.5", 1, 674125),
        ("67412.5", 2, 6741250),
        ("0.00012000", 8, 12000),
        ("3488", 2, 348800),
        ("-1.25", 2, -125),
        ("2.10", 1, 21),
    ],
)
def test_ticks_round_trip_through_strings(text: str, scale: int, ticks: int) -> None:
    assert to_ticks(text, scale) == ticks
    assert to_ticks(from_ticks(ticks, scale), scale) == ticks


def test_to_ticks_refuses_to_drop_precision() -> None:
    with pytest.raises(ValueError):
        to_ticks("1.234", 2)
