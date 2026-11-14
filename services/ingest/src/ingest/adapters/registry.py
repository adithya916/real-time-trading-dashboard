"""Every venue adapter, by name.

Entries are import paths, so each adapter adds exactly one line here and two
branches adding different venues never edit the same line.
"""

from __future__ import annotations

from importlib import import_module

from ingest.adapters.base import FeedAdapter

ADAPTERS: dict[str, str] = {
    "kraken": "ingest.adapters.kraken:KrakenAdapter",
}


def load(name: str) -> type[FeedAdapter]:
    try:
        path = ADAPTERS[name]
    except KeyError:
        known = ", ".join(sorted(ADAPTERS)) or "none yet"
        raise ValueError(f"unknown venue {name!r} (known: {known})") from None
    module_name, _, class_name = path.partition(":")
    adapter: type[FeedAdapter] = getattr(import_module(module_name), class_name)
    return adapter
