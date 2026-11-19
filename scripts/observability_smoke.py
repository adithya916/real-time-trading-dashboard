"""Check Prometheus scrapes every service, the alert rules loaded, and Grafana serves the
dashboard with every one of its queries valid.

    python scripts/observability_smoke.py http://localhost:9090 http://localhost:3000

ingest only runs with the "feeds" profile, so its target may be down; every other target must
be up. Each panel query is run against Prometheus: a typo in a dashboard fails here, not later
as an empty chart.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

OPTIONAL_JOBS = {"ingest"}
DASHBOARD_UID = "md-pipeline"


def get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def query(prometheus: str, expr: str) -> list[Any]:
    body = get(f"{prometheus}/api/v1/query?query={urllib.parse.quote(expr)}")
    if body["status"] != "success":
        raise SystemExit(f"query failed: {expr}: {body}")
    result: list[Any] = body["data"]["result"]
    return result


def main(prometheus: str, grafana: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        targets = get(f"{prometheus}/api/v1/targets")["data"]["activeTargets"]
        down = [
            t["labels"]["job"]
            for t in targets
            if t["health"] != "up" and t["labels"]["job"] not in OPTIONAL_JOBS
        ]
        latency = query(prometheus, 'md_latency_ms{hop="bus_to_gateway"}')
        if not down and latency:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"targets down: {down}; bus_to_gateway samples: {len(latency)}")
        time.sleep(2)
    print(f"prometheus: {len(targets)} targets, all required up")

    rules = get(f"{prometheus}/api/v1/rules")["data"]["groups"]
    names = {r["name"] for g in rules for r in g["rules"]}
    for alert in ("ChecksumFailures", "GatewayLagging", "ServiceDown"):
        if alert not in names:
            raise SystemExit(f"alert rule {alert} not loaded (have {sorted(names)})")
    print(f"prometheus: alert rules loaded: {', '.join(sorted(names))}")

    if get(f"{grafana}/api/health").get("database") != "ok":
        raise SystemExit("grafana is not healthy")
    dashboard = get(f"{grafana}/api/dashboards/uid/{DASHBOARD_UID}")["dashboard"]
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    for expr in exprs:
        query(prometheus, expr)
    with_data = sum(1 for expr in exprs if query(prometheus, expr))
    print(
        f"grafana: '{dashboard['title']}' has {len(dashboard['panels'])} panels; "
        f"{len(exprs)} queries valid, {with_data} returning data now"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
