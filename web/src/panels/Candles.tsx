import { useEffect, useRef } from 'react';
import { createChart, ColorType, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { marketStore } from '../market';

// One-second candles from trades. Updated in place from the store, never through React state.
// TradingView Lightweight Charts asks for attribution, so its logo stays on.
export default function Candles() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const chart = createChart(element, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
      timeScale: { timeVisible: true, secondsVisible: true },
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
    const asBar = (c: {
      time: number;
      open: number;
      high: number;
      low: number;
      close: number;
    }) => ({
      ...c,
      time: c.time as UTCTimestamp,
    });

    let shown = 0; // candles already drawn
    let lastClose = NaN;
    const draw = () => {
      const candles = marketStore.get().candles;
      const last = candles.at(-1);
      if (!last) return;
      if (shown === 0 || candles.length < shown) {
        series.setData(candles.map(asBar));
      } else if (candles.length !== shown || last.close !== lastClose) {
        series.update(asBar(last));
      }
      shown = candles.length;
      lastClose = last.close;
    };
    draw();
    const unsubscribe = marketStore.subscribe(draw);
    return () => {
      unsubscribe();
      chart.remove();
    };
  }, []);

  return <div ref={container} className="h-72 w-full" />;
}
