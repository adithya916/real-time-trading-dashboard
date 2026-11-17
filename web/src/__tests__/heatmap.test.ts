import { describe, expect, it } from 'vitest';
import { cellsFor, priceToY } from '../heatmap';

describe('priceToY', () => {
  it('puts the top of the range at 0 and the bottom at the full height', () => {
    expect(priceToY(110, 100, 10, 200)).toBe(0);
    expect(priceToY(90, 100, 10, 200)).toBe(200);
    expect(priceToY(100, 100, 10, 200)).toBe(100);
  });
});

describe('cellsFor', () => {
  const column = {
    t: 0,
    mid: 100,
    bids: [
      [99.9, 10],
      [99.8, 1],
    ] as [number, number][],
    asks: [[100.1, 0.5]] as [number, number][],
  };

  it('shades bigger levels darker, on a log scale with a visible floor', () => {
    const cells = cellsFor(column, 100, 1, 200);
    const [big, small] = cells.filter((c) => c.side === 'bid');
    expect(big!.alpha).toBe(1);
    expect(small!.alpha).toBeCloseTo(Math.log1p(1) / Math.log1p(10));
    expect(cells.find((c) => c.side === 'ask')!.alpha).toBeGreaterThanOrEqual(0.08);
  });

  it('places asks above bids and fills down to the next level, capped at 8 px', () => {
    const cells = cellsFor(column, 100, 10, 200); // 0.1 apart is 1 px here
    const ask = cells.find((c) => c.side === 'ask')!;
    const [first] = cells.filter((c) => c.side === 'bid');
    expect(ask.y).toBeLessThan(first!.y);
    expect(first!.height).toBeCloseTo(priceToY(99.8, 100, 10, 200) - priceToY(99.9, 100, 10, 200));
    const tight = cellsFor(column, 100, 1, 200).filter((c) => c.side === 'bid')[0]!;
    expect(tight.height).toBe(8); // 10 px apart, capped
  });

  it('leaves out levels far outside the visible range', () => {
    const wide = { ...column, bids: [[50, 1]] as [number, number][] };
    expect(cellsFor(wide, 100, 1, 200).filter((c) => c.side === 'bid')).toEqual([]);
  });
});
