"""Read per-hop latency from a service's /metrics and print it as a table.

    python scripts/metrics_smoke.py http://localhost:8080/metrics bus_to_gateway gateway_hold

Fails unless every named hop has samples. With GITHUB_STEP_SUMMARY set (in CI), the table is
also added to the run's summary page.
"""

import os
import re
import sys
import urllib.request

LINE = re.compile(r'^md_latency_ms\{hop="([^"]+)",quantile="([^"]+)"\} (\S+)$')
COUNT = re.compile(r'^md_latency_samples_total\{hop="([^"]+)"\} (\S+)$')
COLUMNS = [("0.5", "p50"), ("0.99", "p99"), ("0.999", "p99.9"), ("1", "max")]


def main(url: str, required: list[str]) -> None:
    with urllib.request.urlopen(url, timeout=5) as response:
        text = response.read().decode()
    quantiles: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if m := LINE.match(line):
            quantiles.setdefault(m[1], {})[m[2]] = float(m[3])
        elif m := COUNT.match(line):
            counts[m[1]] = int(float(m[2]))
    missing = [hop for hop in required if not counts.get(hop)]
    rows = ["| Hop | Samples | " + " | ".join(name for _, name in COLUMNS) + " |"]
    rows.append("| --- | ---: | " + " | ".join("---:" for _ in COLUMNS) + " |")
    for hop in sorted(quantiles):
        q = quantiles[hop]
        cells = " | ".join(f"{q.get(key, 0):.3f} ms" for key, _ in COLUMNS)
        rows.append(f"| {hop} | {counts.get(hop, 0):,} | {cells} |")
    table = "\n".join(rows)
    print(table)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Latency per hop ({url})\n\n{table}\n\n")
    if missing:
        raise SystemExit(f"no samples for: {', '.join(missing)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
