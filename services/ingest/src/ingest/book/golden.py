"""Golden book hashes: a fingerprint of every book in a stream, every N events.

Replaying a recording must rebuild exactly the books it built live. Each stream is applied to
PyBooks (snapshot, delta, reset). Every level update counts as one event (a snapshot or a
reset counts as one), and each time the count passes a multiple of `every`, plus once at the
end, the book just changed is hashed. A book is also hashed just before a reset clears it,
which pins the complete book each session ended with: SHA-256 over its integer ticks and lots, best level first. Integers make the
hash exact; formatted floats would differ by rounding between machines.

Committed golden files (ingest/tests/golden/*.json) pin those hashes for the fixtures, so any
change that alters a book anywhere in a recording shows up as the first event where it differs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ingest.book.pybook import BookSide, PyBook
from mdcommon.schema import Envelope, Side

EVERY = 1000


def hash_book(book: PyBook) -> str:
    digest = hashlib.sha256()
    for side in ("bid", "ask"):
        digest.update(side.encode())
        for price, size in book.top(side):
            digest.update(f"{price}:{size};".encode())
    return digest.hexdigest()


def book_hashes(envelopes: Iterable[Envelope], every: int = EVERY) -> dict[str, Any]:
    """{"events": n, "every": every, "hashes": [[events so far, venue/symbol, hash], ...]}"""
    books: dict[str, PyBook] = {}
    hashes: list[list[Any]] = []
    events = 0
    for envelope in envelopes:
        kind = envelope.WhichOneof("payload")
        if kind not in ("snapshot", "delta", "reset"):
            continue
        key = f"{envelope.venue}/{envelope.symbol}"
        book = books.setdefault(key, PyBook())
        if kind == "snapshot":
            book.clear()
            for level in envelope.snapshot.bids:
                book.apply("bid", level.price_ticks, level.size_lots)
            for level in envelope.snapshot.asks:
                book.apply("ask", level.price_ticks, level.size_lots)
        elif kind == "delta":
            for update in envelope.delta.updates:
                side: BookSide = "bid" if update.side == Side.SIDE_BID else "ask"
                book.apply(side, update.price_ticks, update.size_lots)
        else:
            # A reset ends a session; pin the whole book it ends with before clearing it.
            if book.bids or book.asks:
                hashes.append([events, key, hash_book(book)])
            book.clear()
        before = events
        events += len(envelope.delta.updates) if kind == "delta" else 1
        if events // every > before // every:
            hashes.append([events, key, hash_book(book)])
    for key, book in sorted(books.items()):
        hashes.append([events, key, hash_book(book)])
    return {"events": events, "every": every, "hashes": hashes}


def first_difference(got: dict[str, Any], want: dict[str, Any]) -> str | None:
    """None when equal, else where they first part ways."""
    for g, w in zip(got["hashes"], want["hashes"], strict=False):
        if g != w:
            return f"book {w[1]} differs at event {w[0]}: {g[2][:12]} != {w[2][:12]}"
    if got != want:
        return f"{got['events']} book events, expected {want['events']}"
    return None


def load_golden(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


def save_golden(path: Path, hashes: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hashes, indent=1) + "\n", encoding="utf-8", newline="\n")
