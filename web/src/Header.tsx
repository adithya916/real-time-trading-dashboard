import { useEffect, useState } from 'react';
import { GATEWAY_HTTP_URL } from './config';
import { fmt } from './format';
import { feedStore, selectFeed, useMarket, type Feed } from './market';
import { useStore } from './store';

const key = (f: Feed) => `${f.venue}/${f.symbol}`;
const REFRESH_FEEDS_MS = 5000;

// Venue and symbol picker (from the gateway's /symbols) and the connection readout.
export default function Header() {
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const selected = useStore(feedStore, (f) => f);
  const connected = useMarket((v) => v.connected);
  const p50 = useMarket((v) => v.rttP50);
  const p99 = useMarket((v) => v.rttP99);
  const fps = useMarket((v) => v.framesPerSecond);
  const seq = useMarket((v) => v.seq);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const response = await fetch(`${GATEWAY_HTTP_URL}/symbols`);
        if (response.ok && !stopped) setFeeds((await response.json()) as Feed[]);
      } catch {
        // The gateway is down; the readout already says so.
      }
    };
    void load();
    const timer = setInterval(load, REFRESH_FEEDS_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  const options = feeds.some((f) => key(f) === key(selected)) ? feeds : [selected, ...feeds];
  const choose = (value: string) => {
    const feed = options.find((f) => key(f) === value);
    if (!feed) return;
    selectFeed(feed);
  };

  return (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">Trading Dashboard</h1>
        <select
          aria-label="Venue and symbol"
          className="rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-sm"
          value={key(selected)}
          onChange={(e) => choose(e.target.value)}
        >
          {options.map((f) => (
            <option key={key(f)} value={key(f)}>
              {f.venue} · {f.symbol}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-4 font-mono text-xs text-slate-400 tabular-nums">
        <span title="WebSocket round trip, including time queued in the gateway">
          RTT p50 {fmt(p50, 1)} ms · p99 {fmt(p99, 1)} ms
        </span>
        <span title="Frames received from the gateway in the last second">{fps} frames/s</span>
        <span title="Sequence number of the last book update applied">seq {seq}</span>
        <span className={connected ? 'text-emerald-400' : 'text-rose-400'}>
          ● {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>
    </header>
  );
}
