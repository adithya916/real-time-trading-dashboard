import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { INTERVAL_S, mergeLive, type Interval } from '../candles';
import { GATEWAY_HTTP_URL } from '../config';
import { feedStore, marketStore } from '../market';
import { useStore } from '../store';
import type { Candle } from '../worker/book';

const HISTORY_BARS = 500;
const REFETCH_MS = 30_000;

async function fetchHistory(venue: string, symbol: string, interval: Interval) {
  const query = new URLSearchParams({ venue, symbol, interval, limit: String(HISTORY_BARS) });
  const response = await fetch(`${GATEWAY_HTTP_URL}/history/candles?${query}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as Candle[];
}

// Candles at 1 s (live, from the worker) or 1 m / 5 m / 1 h (history from the gateway, extended
// with the live one-second candles). Updated in place, never through React state.
// TradingView Lightweight Charts asks for attribution, so its logo stays on.
export default function Candles() {
  const container = useRef<HTMLDivElement>(null);
  const [interval, pick] = useState<Interval>('1s');
  const [note, setNote] = useState('');
  const feed = useStore(feedStore, (f) => f);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const seconds = INTERVAL_S[interval];
    const chart = createChart(element, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
      timeScale: { timeVisible: true, secondsVisible: seconds === 1 },
      // The wheel and vertical swipes scroll the page, not the chart; drag still pans it.
      handleScroll: { mouseWheel: false, vertTouchDrag: false },
      handleScale: { mouseWheel: false },
      autoSize: true,
    });
    const series: ISeriesApi<'Candlestick'> = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
      borderVisible: false,
    });
    const asBar = (c: Candle) => ({ ...c, time: c.time as UTCTimestamp });

    let history: Candle[] = [];
    let since = 0; // live candles from this second on are not in the history yet
    let shown = 0;
    let lastClose = NaN;
    const draw = () => {
      const bars = mergeLive(history, marketStore.get().candles, seconds, since);
      const last = bars.at(-1);
      if (!last) return;
      if (bars.length !== shown) {
        series.setData(bars.map(asBar));
      } else if (last.close !== lastClose || seconds > 1) {
        series.update(asBar(last));
      }
      shown = bars.length;
      lastClose = last.close;
    };

    let stopped = false;
    const load = async () => {
      if (seconds === 1) return;
      try {
        const fetched = await fetchHistory(feed.venue, feed.symbol, interval);
        if (stopped) return;
        history = fetched;
        since = Math.floor(Date.now() / 1000);
        shown = 0; // redraw everything
        setNote(`${fetched.length} bars of history`);
      } catch (error) {
        if (!stopped) setNote(`live only: ${(error as Error).message}`);
      }
      draw();
    };
    setNote(seconds === 1 ? '' : 'loading history…');
    void load();
    const refetch = seconds === 1 ? undefined : setInterval(load, REFETCH_MS);
    draw();
    const unsubscribe = marketStore.subscribe(draw);
    return () => {
      stopped = true;
      clearInterval(refetch);
      unsubscribe();
      chart.remove();
    };
  }, [interval, feed]);

  return (
    <div>
      <div className="mb-2 flex items-center gap-1 text-xs">
        {(Object.keys(INTERVAL_S) as Interval[]).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={option === interval}
            onClick={() => pick(option)}
            className={`rounded px-2 py-0.5 font-mono ${
              option === interval
                ? 'bg-slate-600 text-slate-100'
                : 'text-slate-400 hover:bg-slate-700'
            }`}
          >
            {option}
          </button>
        ))}
        <span className="ml-2 truncate text-slate-500" title={note}>
          {note}
        </span>
      </div>
      <div ref={container} className="h-72 w-full" />
    </div>
  );
}
