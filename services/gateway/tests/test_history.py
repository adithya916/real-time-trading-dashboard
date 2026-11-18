from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
from gateway.app import create_app
from gateway.history import History, Unavailable, candle


def row(minute: int, open_: int, close: int) -> dict[str, Any]:
    return {
        "bucket": datetime(2026, 11, 18, 12, minute, tzinfo=timezone.utc),
        "open_ticks": open_,
        "high_ticks": max(open_, close) + 5,
        "low_ticks": min(open_, close) - 5,
        "close_ticks": close,
        "volume_lots": 150_000_000,
        "trades": 3,
        "price_scale": 1,
        "size_scale": 8,
    }


class FakeHistory(History):
    def __init__(self, rows: list[dict[str, Any]] | None, error: str | None = None) -> None:
        super().__init__("postgresql://unused")
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, str, str, int]] = []

    async def candles(
        self, venue: str, symbol: str, interval: str, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append((venue, symbol, interval, limit))
        if self.error:
            raise Unavailable(self.error)
        return [candle(r) for r in self.rows]


def test_a_row_becomes_a_candle_in_prices_and_sizes() -> None:
    c = candle(row(5, 671_610, 671_632))
    assert c == {
        "time": int(datetime(2026, 11, 18, 12, 5, tzinfo=timezone.utc).timestamp()),
        "open": 67_161.0,
        "high": 67_163.7,
        "low": 67_160.5,
        "close": 67_163.2,
        "volume": 1.5,
        "trades": 3,
    }


def test_the_endpoint_returns_candles_for_the_feed_asked_for() -> None:
    history = FakeHistory([row(1, 10, 20), row(2, 20, 15)])
    with TestClient(create_app(history=history)) as client:
        response = client.get(
            "/history/candles", params={"venue": "kraken", "symbol": "BTC-USD", "interval": "5m"}
        )
    assert response.status_code == 200
    assert [c["close"] for c in response.json()] == [2.0, 1.5]
    assert history.calls == [("kraken", "BTC-USD", "5m", 500)]


def test_unknown_intervals_and_limits_are_rejected() -> None:
    with TestClient(create_app(history=FakeHistory([]))) as client:
        bad_interval = client.get("/history/candles?venue=k&symbol=s&interval=2m")
        bad_limit = client.get("/history/candles?venue=k&symbol=s&limit=0")
    assert bad_interval.status_code == 400
    assert bad_limit.status_code == 422


def test_no_database_means_503_not_a_crash() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/history/candles?venue=kraken&symbol=BTC-USD")
    assert response.status_code == 503
    assert "--db" in response.json()["detail"]


def test_a_failing_database_means_503() -> None:
    with TestClient(create_app(history=FakeHistory(None, "down"))) as client:
        assert client.get("/history/candles?venue=k&symbol=s").status_code == 503
