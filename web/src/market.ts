import { createStore, useStore } from './store';
import type { MarketView } from './worker/book';
import type { FromWorker, ToWorker } from './worker/marketWorker';

export const EMPTY_VIEW: MarketView = {
  connected: false,
  seq: 0,
  bids: [],
  asks: [],
  trades: [],
  metrics: null,
  metricsHistory: [],
  candles: [],
  framesPerSecond: 0,
};

export const marketStore = createStore<MarketView>(EMPTY_VIEW);

let worker: Worker | null = null;

// Start the worker and keep the store updated once per animation frame.
export function startMarket(url: string): () => void {
  worker = new Worker(new URL('./worker/marketWorker.ts', import.meta.url), { type: 'module' });
  worker.onmessage = (event: MessageEvent<FromWorker>) => marketStore.set(event.data.view);
  worker.postMessage({ type: 'connect', url } satisfies ToWorker);
  let frame = requestAnimationFrame(function tick() {
    worker?.postMessage({ type: 'frame' } satisfies ToWorker);
    frame = requestAnimationFrame(tick);
  });
  return () => {
    cancelAnimationFrame(frame);
    worker?.terminate();
    worker = null;
  };
}

export function connectTo(url: string): void {
  worker?.postMessage({ type: 'connect', url } satisfies ToWorker);
}

export const useMarket = <S>(select: (view: MarketView) => S): S => useStore(marketStore, select);
