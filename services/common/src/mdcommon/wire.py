"""How the gateway frames WebSocket messages: one message per tick, many envelopes in it.

Each binary WebSocket message is a size-delimited stream of Envelopes, the standard Protobuf
framing (writeDelimitedTo / parseDelimitedFrom): a varint byte length, then the envelope, then
the next. One message carries everything a viewer gets in one 50 ms tick (a delta or snapshot,
the trades, the latest analytics and strategy state), so the gateway makes one send per viewer
per tick instead of one per envelope, and the same bytes go to every viewer of a symbol.
"""

from __future__ import annotations

from collections.abc import Iterable

from mdcommon.schema import Envelope


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def delimit(frame: bytes) -> bytes:
    """One serialized envelope, prefixed with its length."""
    return _varint(len(frame)) + frame


def encode_batch(frames: Iterable[bytes]) -> bytes:
    return b"".join(delimit(f) for f in frames)


def split_batch(data: bytes) -> list[bytes]:
    """The serialized envelopes in a message, in order. Raises ValueError if it is cut short."""
    frames, i = [], 0
    while i < len(data):
        length = shift = 0
        while True:
            if i >= len(data):
                raise ValueError("message ends inside a length prefix")
            byte = data[i]
            i += 1
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        if i + length > len(data):
            raise ValueError("message ends inside an envelope")
        frames.append(data[i : i + length])
        i += length
    return frames


def decode_batch(data: bytes) -> list[Envelope]:
    return [Envelope.FromString(f) for f in split_batch(data)]
