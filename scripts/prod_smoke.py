"""Check the production stack through Caddy: the dashboard, the API, the WebSocket and Grafana
all on one host, and the internals not exposed.

    python scripts/prod_smoke.py https://localhost

With DOMAIN=localhost Caddy signs with its own local CA, so certificates aren't verified here.
"""

import asyncio
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

import websockets

from mdcommon.schema import Envelope

INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def get(url: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=10, context=INSECURE) as r:
            return r.status, r.headers.get("content-type", ""), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), ""


def wait_for(url: str, timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if get(url)[0] == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"{url} never answered")
        time.sleep(2)


async def first_frame(url: str) -> Envelope:
    async with websockets.connect(url, ssl=INSECURE) as ws:
        return Envelope.FromString(await asyncio.wait_for(ws.recv(), 30))


def main(base: str) -> None:
    wait_for(f"{base}/health")
    status, kind, body = get(f"{base}/")
    assert status == 200 and "text/html" in kind and '<div id="root">' in body, (status, kind)
    print("caddy: dashboard served")

    deadline = time.monotonic() + 60
    while "BTC-SIM" not in get(f"{base}/symbols")[2]:
        if time.monotonic() > deadline:
            raise SystemExit("the simulated feed never showed up in /symbols")
        time.sleep(2)
    print(f"caddy: /symbols -> {json.loads(get(f'{base}/symbols')[2])}")

    ws_url = base.replace("https://", "wss://") + "/ws/sim/BTC-SIM"
    frame = asyncio.run(first_frame(ws_url))
    assert frame.HasField("snapshot"), frame.WhichOneof("payload")
    print(f"caddy: wss stream -> snapshot at seq {frame.seq}")

    status, _, body = get(f"{base}/grafana/api/health")
    assert status == 200 and json.loads(body)["database"] == "ok", (status, body)
    print("caddy: grafana under /grafana")

    # Internals stay private: /metrics falls through to the dashboard, never Prometheus text.
    _, kind, body = get(f"{base}/metrics")
    assert "md_latency" not in body and "text/plain" not in kind, kind
    print("caddy: /metrics not exposed")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "https://localhost")
