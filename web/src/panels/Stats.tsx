import { useMarket } from '../market';
import { fmt } from '../format';

export default function Stats() {
  const m = useMarket((v) => v.metrics);
  const imbalanceColor = (m?.imbalanceL1 ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400';
  const cards: [string, string, string][] = [
    ['Mid', fmt(m?.mid), 'text-white'],
    ['Microprice', fmt(m?.microprice), 'text-sky-300'],
    ['Spread', m ? `${fmt(m.spreadBps)} bps` : '---', 'text-amber-300'],
    ['Imbalance (L1)', fmt(m?.imbalanceL1), imbalanceColor],
    ['Realized vol (60 s)', m ? `${fmt(m.realizedVol60s * 10_000)} bps` : '---', 'text-slate-200'],
    ['Session VWAP', fmt(m?.vwapSession), 'text-indigo-300'],
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {cards.map(([label, value, color]) => (
        <div key={label} className="rounded-lg border border-slate-700 bg-slate-800 p-3">
          <div className="text-xs text-slate-400">{label}</div>
          <div className={`font-mono text-lg font-semibold tabular-nums ${color}`}>{value}</div>
        </div>
      ))}
    </div>
  );
}
