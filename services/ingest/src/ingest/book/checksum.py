"""Kraken's order book checksum (WebSocket v2).

CRC32 over the top 10 asks (lowest first) followed by the top 10 bids (highest first).
Each price and quantity is written with the instrument's precision, then the decimal point
and any leading zeros are removed. A match proves the local book equals Kraken's.
"""

from __future__ import annotations

import zlib

from ingest.book.pybook import Level, PyBook
from mdcommon.schema import from_ticks

CHECKSUM_LEVELS = 10


def _digits(ticks: int, scale: int) -> str:
    return from_ticks(ticks, scale).replace(".", "").lstrip("0")


def _side(levels: list[Level], price_scale: int, size_scale: int) -> str:
    return "".join(_digits(p, price_scale) + _digits(q, size_scale) for p, q in levels)


def kraken_checksum(book: PyBook, price_scale: int, size_scale: int) -> int:
    payload = _side(book.top("ask", CHECKSUM_LEVELS), price_scale, size_scale) + _side(
        book.top("bid", CHECKSUM_LEVELS), price_scale, size_scale
    )
    return zlib.crc32(payload.encode("ascii"))
