import { fmt } from '../format';
import { useMarket } from '../market';
import type { MarketView, Strategy } from '../worker/book';

const WIDTH = 300;
const HEIGHT = 80;

// Total PnL over time as an SVG line, with zero marked. Rebuilt only when a new state arrives.
function sparkline(points: [number, number][]): { path: string; zeroY: number } | null {
  if (points.length < 2) return null;
  const t0 = points[0]![0];
  const t1 = points.at(-1)![0];
  const values = points.map(([, v]) => v);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo || 1;
  const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * WIDTH;
  const y = (v: number) => HEIGHT - ((v - lo) / span) * HEIGHT;
  const path = points
    .map(([t, v], i) => `${i ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`)
    .join('');
  return { path, zeroY: y(0) };
}

// Views arrive every frame as new objects, but the strategy changes about twice a second.
// The selector returns the same object until a new state arrives, so the panel re-renders
// only then (and the store's rule, a selector returns something it already returned, holds).
interface Shown {
  ts: number;
  strategy: Strategy | null;
  line: ReturnType<typeof sparkline>;
}
let shown: Shown = { ts: -1, strategy: null, line: null };
function select(view: MarketView): Shown {
  const ts = view.strategy?.ts ?? 0;
  if (ts !== shown.ts) shown = { ts, strategy: view.strategy, line: sparkline(view.pnlHistory) };
  return shown;
}

const signed = (v: number | undefined) =>
  v === undefined ? '---' : `${v >= 0 ? '+' : ''}${fmt(v)}`;
const tone = (v: number | undefined) =>
  v === undefined ? 'text-slate-300' : v >= 0 ? 'text-emerald-400' : 'text-rose-400';

// The paper trader's position, PnL and quotes, from its StrategyState on this feed.
export default function Pnl() {
  const { strategy: s, line } = useMarket(select);

  if (!s) {
    return (
      <p className="text-sm text-slate-400">
        No strategy trades this feed. The paper trader market-makes <code>sim · BTC-SIM</code>.
      </p>
    );
  }
  const total = s.realized + s.unrealized;
  const rows: [string, string, string][] = [
    ['Position', fmt(s.position, 4), s.position >= 0 ? 'text-emerald-300' : 'text-rose-300'],
    ['Realized', signed(s.realized), tone(s.realized)],
    ['Unrealized', signed(s.unrealized), tone(s.unrealized)],
    ['Total PnL', signed(total), tone(total)],
    ['Quotes', `${fmt(s.bid ?? undefined)} / ${fmt(s.ask ?? undefined)}`, 'text-slate-200'],
    ['Fills', `${s.fills} (${fmt(s.traded, 4)} traded)`, 'text-slate-200'],
  ];
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span className="font-mono">{s.name}</span>
        {s.halted ? (
          <span className="rounded bg-rose-900/60 px-2 py-0.5 text-rose-200" title={s.haltReason}>
            halted
          </span>
        ) : (
          <span className="rounded bg-emerald-900/50 px-2 py-0.5 text-emerald-200">quoting</span>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-sm tabular-nums">
        {rows.map(([label, value, color]) => (
          <div key={label} className="contents">
            <dt className="text-slate-400">{label}</dt>
            <dd className={`text-right ${color}`}>{value}</dd>
          </div>
        ))}
      </dl>
      {line && (
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-20 w-full"
          preserveAspectRatio="none"
          role="img"
          aria-label="Total PnL over time"
        >
          <line
            x1={0}
            x2={WIDTH}
            y1={line.zeroY}
            y2={line.zeroY}
            stroke="#475569"
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={line.path}
            fill="none"
            stroke={total >= 0 ? '#34d399' : '#fb7185'}
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      )}
      {s.halted && <p className="text-xs text-rose-300">{s.haltReason}</p>}
    </div>
  );
}
