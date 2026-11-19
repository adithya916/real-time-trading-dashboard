"""Plot load-test CSVs (bench/wsload) as an SVG: p99 round trip against viewers asked for.

    python scripts/plot_load.py bench/results/wsload.svg "one message per tick=bench/results/wsload.csv" \
        "one send per envelope=bench/results/wsload-per-envelope.csv"

No plotting library: a few dozen lines of SVG, log scale on the y axis, a point per step, and
steps where viewers failed to connect drawn hollow.
"""

import csv
import math
import sys
from pathlib import Path

W, H, LEFT, RIGHT, TOP, BOTTOM = 720, 420, 70, 200, 40, 60
COLORS = ["#0f766e", "#c2410c", "#4338ca", "#a16207"]


def load(path: str) -> list[dict[str, float]]:
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def main(out: str, series_specs: list[str]) -> None:
    series = []
    for spec in series_specs:
        label, _, path = spec.partition("=")
        series.append((label, load(path)))
    xs = [r["viewers"] for _, rows in series for r in rows]
    ys = [r["rtt_p99_ms"] for _, rows in series for r in rows]
    x_max = max(xs) * 1.05
    y_lo, y_hi = 10 ** math.floor(math.log10(min(ys))), 10 ** math.ceil(math.log10(max(ys)))

    def px(x: float) -> float:
        return LEFT + x / x_max * (W - LEFT - RIGHT)

    def py(y: float) -> float:
        span = math.log10(y_hi) - math.log10(y_lo)
        return TOP + (1 - (math.log10(max(y, y_lo)) - math.log10(y_lo)) / span) * (H - TOP - BOTTOM)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{LEFT}" y="22" font-size="14" font-weight="600" fill="#0f172a">WebSocket round trip p99 vs viewers, one gateway process</text>',
    ]
    decade = y_lo
    while decade <= y_hi:
        y = py(decade)
        parts.append(
            f'<line x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#475569">{decade:g} ms</text>'
        )
        decade *= 10
    step = 10 ** math.floor(math.log10(x_max / 4))
    tick = 0.0
    while tick <= x_max:
        x = px(tick)
        parts.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{H - BOTTOM}" stroke="#f1f5f9"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{H - BOTTOM + 18}" text-anchor="middle" fill="#475569">{tick:,.0f}</text>'
        )
        tick += step * (2 if x_max / step > 8 else 1)
    parts.append(
        f'<text x="{(LEFT + W - RIGHT) / 2}" y="{H - 16}" text-anchor="middle" fill="#334155">viewers asked for (hollow: some failed to connect)</text>'
    )
    for i, (label, rows) in enumerate(series):
        color = COLORS[i % len(COLORS)]
        points = " ".join(f"{px(r['viewers']):.1f},{py(r['rtt_p99_ms']):.1f}" for r in rows)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in rows:
            fill = color if r["errors"] == 0 else "#ffffff"
            parts.append(
                f'<circle cx="{px(r["viewers"]):.1f}" cy="{py(r["rtt_p99_ms"]):.1f}" r="4" fill="{fill}" stroke="{color}" stroke-width="2">'
                f'<title>{label}: {r["viewers"]:,.0f} asked, {r["connected"]:,.0f} connected, p99 {r["rtt_p99_ms"]:.1f} ms</title></circle>'
            )
        ly = TOP + 10 + i * 22
        parts.append(
            f'<line x1="{W - RIGHT + 16}" x2="{W - RIGHT + 36}" y1="{ly}" y2="{ly}" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(f'<text x="{W - RIGHT + 42}" y="{ly + 4}" fill="#0f172a">{label}</text>')
    parts.append("</svg>")
    Path(out).write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
