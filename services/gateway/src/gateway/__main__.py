"""Run the gateway: python -m gateway --bus nats://localhost:4222 --port 8080"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from gateway.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket gateway for the market data bus.")
    parser.add_argument("--bus", default="nats://localhost:4222")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(bus_url=args.bus), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
