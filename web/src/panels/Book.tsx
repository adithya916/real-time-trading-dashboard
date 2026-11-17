import { marketStore, useMarket } from '../market';
import { fmt } from '../format';
import type { Level } from '../worker/book';

const DEPTH = 10;

function Row({ level, max, side }: { level: Level; max: number; side: 'bid' | 'ask' }) {
  const [price, size] = level;
  const color = side === 'bid' ? 'text-emerald-400' : 'text-rose-400';
  const bar = side === 'bid' ? 'bg-emerald-500/15' : 'bg-rose-500/15';
  return (
    <div className="relative flex justify-between px-2 py-0.5 font-mono text-sm tabular-nums">
      <div
        className={`absolute inset-y-0 right-0 ${bar}`}
        style={{ width: `${(size / max) * 100}%` }}
      />
      <span className={`relative ${color}`}>{fmt(price)}</span>
      <span className="relative text-slate-300">{fmt(size, 4)}</span>
    </div>
  );
}

export default function Book() {
  useMarket((v) => v.seq); // re-render when the book changes, then read it once
  const { bids, asks } = marketStore.get();
  const shownAsks = asks.slice(0, DEPTH).reverse();
  const shownBids = bids.slice(0, DEPTH);
  const max = Math.max(1e-12, ...shownAsks.map((l) => l[1]), ...shownBids.map((l) => l[1]));
  const spread = asks[0] && bids[0] ? asks[0][0] - bids[0][0] : null;
  return (
    <div>
      {shownAsks.map((l) => (
        <Row key={`a${l[0]}`} level={l} max={max} side="ask" />
      ))}
      <div className="border-y border-dashed border-slate-700 py-1 text-center text-xs text-slate-500">
        spread {fmt(spread)}
      </div>
      {shownBids.map((l) => (
        <Row key={`b${l[0]}`} level={l} max={max} side="bid" />
      ))}
    </div>
  );
}
