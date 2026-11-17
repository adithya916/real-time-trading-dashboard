import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { marketStore } from '../market';

// Mid, microprice and session VWAP over the last 60 s, drawn on canvas by uPlot. The chart is
// updated in place when new analytics arrive (10 a second); React never re-renders it.
export default function PriceChart() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const axis = { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#1e293b' } };
    const plot = new uPlot(
      {
        width: element.clientWidth,
        height: 288,
        legend: { show: true },
        scales: { x: { time: true } },
        axes: [axis, { ...axis, size: 80 }],
        series: [
          {},
          { label: 'Mid', stroke: '#10b981', width: 1.5 },
          { label: 'Microprice', stroke: '#38bdf8', width: 1 },
          { label: 'VWAP', stroke: '#818cf8', width: 1, dash: [6, 4] },
        ],
      },
      [[], [], [], []],
      element,
    );

    let lastTs = 0;
    const draw = () => {
      const history = marketStore.get().metricsHistory;
      const newest = history.at(-1)?.ts ?? 0;
      if (newest === lastTs) return;
      lastTs = newest;
      plot.setData([
        history.map((m) => m.ts / 1000),
        history.map((m) => m.mid),
        history.map((m) => m.microprice),
        history.map((m) => m.vwapSession || null),
      ]);
    };
    const resize = new ResizeObserver(() =>
      plot.setSize({ width: element.clientWidth, height: 288 }),
    );
    resize.observe(element);
    const unsubscribe = marketStore.subscribe(draw);
    return () => {
      unsubscribe();
      resize.disconnect();
      plot.destroy();
    };
  }, []);

  return <div ref={container} className="w-full" />;
}
