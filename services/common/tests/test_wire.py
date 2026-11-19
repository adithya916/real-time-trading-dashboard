import pytest
from hypothesis import given
from hypothesis import strategies as st

from mdcommon.schema import Envelope, Trade
from mdcommon.wire import decode_batch, delimit, encode_batch, split_batch


@given(st.lists(st.binary(max_size=400), max_size=20))
def test_frames_round_trip(frames: list[bytes]) -> None:
    assert split_batch(encode_batch(frames)) == frames


def test_lengths_over_127_bytes_take_two_varint_bytes() -> None:
    assert delimit(b"x" * 300)[:2] == bytes([0xAC, 0x02])


def test_envelopes_decode_in_order() -> None:
    envelopes = [Envelope(venue="sim", symbol="BTC-SIM", seq=i) for i in (1, 2, 3)]
    envelopes[1].trade.CopyFrom(Trade(trade_id="7"))
    decoded = decode_batch(encode_batch(e.SerializeToString() for e in envelopes))
    assert [e.seq for e in decoded] == [1, 2, 3] and decoded[1].trade.trade_id == "7"


@pytest.mark.parametrize("cut", [1, 5])
def test_a_truncated_message_is_rejected(cut: int) -> None:
    data = encode_batch([b"a" * 200, b"bb"])
    with pytest.raises(ValueError):
        split_batch(data[:-cut] if cut > 1 else data[:1])
