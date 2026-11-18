import type { Candle } from './worker/book';

// Candle intervals the chart offers. One second is built live in the worker; the others start
// from the gateway's history (TimescaleDB) and are extended with the live one-second candles.
export const INTERVAL_S = { '1s': 1, '1m': 60, '5m': 300, '1h': 3600 } as const;
export type Interval = keyof typeof INTERVAL_S;

export const bucketOf = (time: number, seconds: number) => Math.floor(time / seconds) * seconds;

// History bars as fetched, then live one-second candles from `since` on folded into bars of
// `seconds`: into the last history bar when they fall in it, into new bars after it. Live
// candles before `since` are already counted in the history.
export function mergeLive(
  history: readonly Candle[],
  live: readonly Candle[],
  seconds: number,
  since: number,
): Candle[] {
  const bars = history.map((bar) => ({ ...bar }));
  for (const candle of live) {
    if (candle.time < since) continue;
    const time = bucketOf(candle.time, seconds);
    const last = bars.at(-1);
    if (last && last.time === time) {
      last.high = Math.max(last.high, candle.high);
      last.low = Math.min(last.low, candle.low);
      last.close = candle.close;
      last.volume += candle.volume;
    } else if (!last || time > last.time) {
      bars.push({ ...candle, time });
    }
  }
  return bars;
}
