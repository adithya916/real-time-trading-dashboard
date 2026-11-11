import main_server
import orjson
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("symbol", ["AAPL", "tsla"])
def test_stream_only_sends_the_requested_symbol(client, symbol):
    with client.websocket_connect(f"/ws/stream/{symbol}") as ws:
        messages = [orjson.loads(ws.receive_text()) for _ in range(50)]

    assert {m["symbol"] for m in messages} == {symbol.upper()}
    assert all(m["analytics"]["vwap"] > 0 for m in messages)


def test_unknown_symbol_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/stream/NOPE") as ws:
            ws.receive_text()
    assert excinfo.value.code == 1008


def test_shutdown_does_not_hang(fake_redis):
    # Regression: redis 5.0.1 could swallow the cancel and hang shutdown on Python 3.10.
    # pytest-timeout fails this test if leaving the context manager blocks.
    with TestClient(main_server.app) as client:
        assert client.get("/health").status_code == 200
