import type { DepthColumn } from './worker/book';

// Drawing maths for the depth heatmap, kept free of the canvas so it can be tested.

export interface Cell {
  y: number;
  height: number;
  alpha: number; // 0..1, from size on a log scale
  side: 'bid' | 'ask';
}

// Price to a y coordinate: center + halfRange at the top, center - halfRange at the bottom.
export const priceToY = (price: number, center: number, halfRange: number, height: number) =>
  ((center + halfRange - price) / (2 * halfRange)) * height;

export function cellsFor(
  column: DepthColumn,
  center: number,
  halfRange: number,
  height: number,
): Cell[] {
  const sizes = [...column.bids, ...column.asks].map(([, size]) => size);
  const maxLog = Math.log1p(Math.max(...sizes, 0)) || 1;
  const cells: Cell[] = [];
  for (const [side, levels] of [
    ['bid', column.bids],
    ['ask', column.asks],
  ] as const) {
    levels.forEach(([price, size], i) => {
      const y = priceToY(price, center, halfRange, height);
      const next = levels[i + 1];
      // Fill down to the next level, so gaps in the book show as gaps.
      const gap = next ? Math.abs(priceToY(next[0], center, halfRange, height) - y) : 2;
      const alpha = Math.min(1, Math.max(0.08, Math.log1p(size) / maxLog));
      if (y >= -gap && y <= height + gap) {
        cells.push({ y, height: Math.min(Math.max(gap, 1), 8), alpha, side });
      }
    });
  }
  return cells;
}
