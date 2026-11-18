import { describe, expect, it } from 'vitest';
import { bucketOf, mergeLive } from '../candles';
import type { Candle } from '../worker/book';

const bar = (time: number, open: number, high: number, low: number, close: number): Candle => ({
  time,
  open,
  high,
  low,
  close,
  volume: 1,
});

describe('bucketOf', () => {
  it('floors to the start of the interval', () => {
    expect(bucketOf(125, 60)).toBe(120);
    expect(bucketOf(120, 60)).toBe(120);
    expect(bucketOf(3599, 3600)).toBe(0);
  });
});

describe('mergeLive', () => {
  const history = [bar(0, 10, 12, 9, 11), bar(60, 11, 13, 10, 12)];

  it('extends the last history bar with live candles inside it, keeping its open', () => {
    const merged = mergeLive(history, [bar(100, 12, 15, 12, 14)], 60, 90);
    expect(merged).toHaveLength(2);
    expect(merged[1]).toEqual({ time: 60, open: 11, high: 15, low: 10, close: 14, volume: 2 });
  });

  it('starts new bars after the history and aggregates within each', () => {
    const live = [bar(125, 14, 16, 14, 15), bar(130, 15, 15, 8, 9), bar(185, 9, 9, 9, 9)];
    const merged = mergeLive(history, live, 60, 90);
    expect(merged.map((b) => b.time)).toEqual([0, 60, 120, 180]);
    expect(merged[2]).toEqual({ time: 120, open: 14, high: 16, low: 8, close: 9, volume: 2 });
  });

  it('skips live candles from before the history was fetched', () => {
    const merged = mergeLive(history, [bar(70, 99, 99, 99, 99)], 60, 90);
    expect(merged[1]!.high).toBe(13);
  });

  it('builds bars from live candles alone when there is no history', () => {
    const merged = mergeLive([], [bar(5, 1, 2, 1, 2), bar(30, 2, 3, 0, 1)], 60, 0);
    expect(merged).toEqual([{ time: 0, open: 1, high: 3, low: 0, close: 1, volume: 2 }]);
  });

  it('never changes the history it was given', () => {
    mergeLive(history, [bar(100, 12, 99, 12, 14)], 60, 0);
    expect(history[1]!.high).toBe(13);
  });

  it('with one-second bars and no history, returns the live candles as they are', () => {
    const live = [bar(1, 1, 1, 1, 1), bar(2, 2, 2, 2, 2)];
    expect(mergeLive([], live, 1, 0)).toEqual(live);
  });
});
