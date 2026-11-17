import { useEffect, useRef } from 'react';
import { cellsFor, priceToY } from '../heatmap';
import { marketStore } from '../market';

const HEIGHT = 288;
const COLUMN_PX = 3;
const HALF_RANGE = 0.0015; // show the mid ± 15 bps
const RECENTER_AT = 0.7; // recenter when the mid drifts this far toward an edge

// Depth over time: each 250 ms column shows the top 25 levels per side, shaded by size, with
// the mid as a white line. New columns scroll in from the right; old ones scroll off.
export default function Heatmap() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const fit = () => {
      canvas.width = Math.floor(canvas.clientWidth * dpr);
      canvas.height = Math.floor(HEIGHT * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    fit();

    let lastT = 0;
    let center = 0;
    let lastMidY: number | null = null;
    const draw = () => {
      const column = marketStore.get().depthColumn;
      if (!column || column.t === lastT) return;
      lastT = column.t;
      const width = canvas.clientWidth;
      const halfRange = column.mid * HALF_RANGE;
      if (!center || Math.abs(column.mid - center) > halfRange * RECENTER_AT) {
        center = column.mid; // start over around the new price
        ctx.clearRect(0, 0, width, HEIGHT);
        lastMidY = null;
      }
      // Scroll everything one column left, then paint the new column on the right.
      ctx.drawImage(canvas, -COLUMN_PX * dpr, 0);
      const x = width - COLUMN_PX;
      ctx.clearRect(x, 0, COLUMN_PX, HEIGHT);
      for (const cell of cellsFor(column, center, halfRange, HEIGHT)) {
        ctx.fillStyle =
          cell.side === 'bid' ? `rgba(16,185,129,${cell.alpha})` : `rgba(244,63,94,${cell.alpha})`;
        ctx.fillRect(x, cell.y, COLUMN_PX, cell.height);
      }
      const midY = priceToY(column.mid, center, halfRange, HEIGHT);
      ctx.strokeStyle = '#f8fafc';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - COLUMN_PX / 2, lastMidY ?? midY);
      ctx.lineTo(x + COLUMN_PX / 2, midY);
      ctx.stroke();
      lastMidY = midY;
    };
    const resize = new ResizeObserver(() => {
      fit();
      center = 0; // redraw from scratch at the new size
    });
    resize.observe(canvas);
    const unsubscribe = marketStore.subscribe(draw);
    return () => {
      unsubscribe();
      resize.disconnect();
    };
  }, []);

  return <canvas ref={canvasRef} className="block w-full" style={{ height: HEIGHT }} />;
}
