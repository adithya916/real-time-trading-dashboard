import { marketStore, useMarket } from '../market';
import { fmt } from '../format';

export default function Trades() {
  useMarket((v) => v.trades[0]?.id ?? ''); // re-render only when a new trade arrives
  const trades = marketStore.get().trades.slice(0, 20);
  return (
    <div className="max-h-80 overflow-y-auto font-mono text-sm tabular-nums">
      {trades.map((t) => (
        <div key={t.id} className="flex justify-between px-2 py-0.5">
          <span className={t.side === 'buy' ? 'text-emerald-400' : 'text-rose-400'}>
            {fmt(t.price)}
          </span>
          <span className="text-slate-300">{fmt(t.size, 4)}</span>
          <span className="text-slate-500">{new Date(t.ts).toLocaleTimeString()}</span>
        </div>
      ))}
    </div>
  );
}
