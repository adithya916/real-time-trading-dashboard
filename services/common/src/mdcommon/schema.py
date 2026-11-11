"""The market data schema, re-exported from the generated Protobuf module.

Run scripts/gen_proto.sh first; the generated code is not committed.
"""

from mdcommon.gen.marketdata.v1.marketdata_pb2 import (
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
)

__all__ = [
    "Aggressor",
    "Analytics",
    "BookReset",
    "Delta",
    "Envelope",
    "Level",
    "LevelUpdate",
    "ResetReason",
    "Side",
    "Snapshot",
    "Trade",
]


def to_ticks(value: str, scale: int) -> int:
    """Convert a decimal string from a venue into an integer at the given scale.

    Works on the string so no float rounding sneaks in: to_ticks("67412.5", 2) == 6741250.
    """
    whole, _, frac = value.partition(".")
    if len(frac) > scale:
        if frac[scale:].strip("0"):
            raise ValueError(f"{value!r} has more than {scale} decimal places")
        frac = frac[:scale]
    sign = -1 if whole.startswith("-") else 1
    digits = whole.lstrip("+-") + frac.ljust(scale, "0")
    return sign * int(digits or "0")


def from_ticks(ticks: int, scale: int) -> str:
    """Inverse of to_ticks, for display and venue checksums: from_ticks(6741250, 2) == "67412.50"."""
    sign = "-" if ticks < 0 else ""
    digits = str(abs(ticks)).rjust(scale + 1, "0")
    if scale == 0:
        return sign + digits
    return f"{sign}{digits[:-scale]}.{digits[-scale:]}"
