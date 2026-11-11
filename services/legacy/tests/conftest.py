import asyncio

import fakeredis
import main_server
import pytest
import redis.asyncio.client as redis_client_module
from fastapi.testclient import TestClient


@pytest.fixture
def fake_redis(monkeypatch):
    """Point the server at an in-memory Redis.

    fakeredis never yields to the event loop the way a real socket round trip does,
    so the publish pipeline would starve every other task. One sleep(0) per
    execute() stands in for that round trip.
    """
    server = fakeredis.FakeServer()
    original_execute = redis_client_module.Pipeline.execute

    async def execute_with_yield(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await original_execute(self, *args, **kwargs)

    monkeypatch.setattr(redis_client_module.Pipeline, "execute", execute_with_yield)
    monkeypatch.setattr(
        main_server.redis,
        "from_url",
        lambda *args, **kwargs: fakeredis.FakeAsyncRedis(server=server, decode_responses=True),
    )
    return server


@pytest.fixture
def client(fake_redis):
    with TestClient(main_server.app) as test_client:
        yield test_client
