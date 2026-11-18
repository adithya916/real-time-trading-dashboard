"""The FastAPI app: the bus loop, the 50 ms tick, and one WebSocket per viewer.

    GET /health
    GET /symbols                          venues and symbols with an in-sync book
    WS  /ws/{venue}/{symbol}              binary marketdata.v1 Envelope frames

A viewer first gets a Snapshot, then per tick at most one Delta (every level that changed
since the previous frame, at its latest size), every Trade, and the latest Analytics.
A text "ping:<token>" from the viewer comes back as the same text, after its queued frames.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from gateway.hub import CLOSE_FRAME, Hub, Key
from mdcommon.schema import Envelope

log = logging.getLogger(__name__)
TICK_S = 0.05
RESYNC_RETRY_S = 1.0


async def tick_forever(hub: Hub) -> None:
    while True:
        await asyncio.sleep(TICK_S)
        hub.tick(time.monotonic())


async def bus_forever(hub: Hub, bus_url: str) -> None:
    import nats

    from mdcommon.bus import SNAPSHOT_BUCKET, ensure_stream, snapshot_key

    nc = await nats.connect(bus_url, name="gateway", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await ensure_stream(js)
    kv = await js.key_value(SNAPSHOT_BUCKET)
    resyncing: set[Key] = set()

    async def resync(key: Key) -> None:
        # Retry until a bucket snapshot plus the buffered envelopes close the gap.
        try:
            while not hub.mirror(key).synced:
                try:
                    entry = await kv.get(snapshot_key(*key))
                    if entry.value:
                        hub.mirror(key).load_snapshot(Envelope.FromString(entry.value))
                except Exception as exc:  # no snapshot yet, or the bucket is unreachable
                    log.debug("resync %s: %r", key, exc)
                if not hub.mirror(key).synced:
                    await asyncio.sleep(RESYNC_RETRY_S)
        finally:
            resyncing.discard(key)

    def start_resync(key: Key) -> None:
        if key not in resyncing:
            resyncing.add(key)
            asyncio.get_running_loop().create_task(resync(key))

    async def on_message(msg: Any) -> None:
        envelope = Envelope.FromString(msg.data)
        key = (envelope.venue, envelope.symbol)
        new = key not in hub.mirrors
        if hub.on_envelope(envelope) or (new and not hub.mirror(key).synced):
            start_resync(key)

    # One subscription keeps book and trade envelopes in publish order.
    await nc.subscribe("md.*.*.*", cb=on_message)
    log.info("gateway: subscribed to md.*.*.* on %s", bus_url)
    try:
        await asyncio.Event().wait()
    finally:
        await nc.drain()


def create_app(
    hub: Hub | None = None,
    bus_url: str | None = None,
    background: Sequence[Callable[[], Coroutine[Any, Any, Any]]] = (),
) -> FastAPI:
    """`background` coroutines run for the app's lifetime, e.g. the demo replay."""
    hub = hub or Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tasks = [asyncio.create_task(tick_forever(hub))]
        if bus_url:
            tasks.append(asyncio.create_task(bus_forever(hub, bus_url)))
        tasks += [asyncio.create_task(start()) for start in background]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/symbols")
    async def symbols() -> list[dict[str, str]]:
        return [{"venue": v, "symbol": s} for (v, s), m in sorted(hub.mirrors.items()) if m.synced]

    @app.websocket("/ws/{venue}/{symbol}")
    async def stream(websocket: WebSocket, venue: str, symbol: str) -> None:
        await websocket.accept()
        viewer = hub.add_viewer((venue, symbol))

        async def send() -> None:
            while True:
                frame = await viewer.queue.get()
                if frame == CLOSE_FRAME:
                    await websocket.close(code=1013, reason="too slow to keep up")
                    return
                if isinstance(frame, str):
                    await websocket.send_text(frame)
                else:
                    await websocket.send_bytes(frame)

        async def receive() -> None:
            # The only thing viewers send is "ping:<anything>". The reply goes through the same
            # queue as the market data, so the round trip includes any queueing here.
            try:
                while True:
                    text = await websocket.receive_text()
                    if text.startswith("ping:"):
                        try:
                            viewer.queue.put_nowait(text)
                        except asyncio.QueueFull:
                            pass  # a viewer this far behind is being resynced anyway
            except WebSocketDisconnect:
                return

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            hub.remove_viewer(viewer)

    return app
