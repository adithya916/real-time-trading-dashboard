import { fromBinary } from '@bufbuild/protobuf';
import { EnvelopeSchema } from '../gen/marketdata/v1/marketdata_pb';

// Everything the worker keeps for one venue and symbol, and the slice it posts to the page.

export type Level = [price: number, size: number];

export interface TradePrint {
  id: string;
  price: number;
  size: number;
  side: 'buy' | 'sell';
  ts: number; // ms
}

export interface Metrics {
  mid: number;
  microprice: number;
  spreadBps: number;
  imbalanceL1: number;
  imbalanceL5: number;
  realizedVol60s: number;
  vwapSession: number;
  twap60s: number;
  ts: number; // ms
}

// What the page renders, posted once per animation frame.
export interface MarketView {
  connected: boolean;
  seq: number;
  bids: Level[]; // best first, prices and sizes in real units
  asks: Level[];
  trades: TradePrint[]; // newest first
  metrics: Metrics | null;
  metricsHistory: Metrics[]; // oldest first
  framesPerSecond: number;
}

const TRADES_KEPT = 60;
const METRICS_KEPT = 600; // 60 s at 10 Hz

export class MarketState {
  private bids = new Map<number, number>(); // ticks -> lots
  private asks = new Map<number, number>();
  private priceScale = 1;
  private sizeScale = 1;
  private synced = false;
  connected = false;
  seq = 0;
  trades: TradePrint[] = [];
  metricsHistory: Metrics[] = [];
  private frameTimes: number[] = [];

  apply(bytes: Uint8Array, nowMs: number): void {
    const envelope = fromBinary(EnvelopeSchema, bytes);
    this.frameTimes.push(nowMs);
    const payload = envelope.payload;
    switch (payload.case) {
      case 'snapshot': {
        this.bids = new Map(
          payload.value.bids.map((l) => [Number(l.priceTicks), Number(l.sizeLots)]),
        );
        this.asks = new Map(
          payload.value.asks.map((l) => [Number(l.priceTicks), Number(l.sizeLots)]),
        );
        this.priceScale = 10 ** payload.value.priceScale;
        this.sizeScale = 10 ** payload.value.sizeScale;
        this.synced = true;
        this.seq = Number(envelope.seq);
        break;
      }
      case 'delta': {
        if (!this.synced) return;
        for (const update of payload.value.updates) {
          const side = update.side === 1 ? this.bids : this.asks;
          const price = Number(update.priceTicks);
          const size = Number(update.sizeLots);
          if (size === 0) side.delete(price);
          else side.set(price, size);
        }
        this.seq = Number(envelope.seq);
        break;
      }
      case 'trade': {
        if (!this.synced) return;
        this.trades.unshift({
          id: payload.value.tradeId,
          price: Number(payload.value.priceTicks) / this.priceScale,
          size: Number(payload.value.sizeLots) / this.sizeScale,
          side: payload.value.aggressor === 1 ? 'buy' : 'sell',
          ts: Number(envelope.tsExchNs / 1_000_000n) || nowMs,
        });
        this.trades.length = Math.min(this.trades.length, TRADES_KEPT);
        break;
      }
      case 'analytics': {
        const a = payload.value;
        this.metricsHistory.push({
          mid: a.mid,
          microprice: a.microprice,
          spreadBps: a.spreadBps,
          imbalanceL1: a.imbalanceL1,
          imbalanceL5: a.imbalanceL5,
          realizedVol60s: a.realizedVol60s,
          vwapSession: a.vwapSession,
          twap60s: a.twap60s,
          ts: nowMs,
        });
        if (this.metricsHistory.length > METRICS_KEPT) this.metricsHistory.shift();
        break;
      }
      case 'reset':
        this.synced = false;
        break;
    }
  }

  top(side: 'bid' | 'ask', n: number): Level[] {
    const levels = side === 'bid' ? this.bids : this.asks;
    return [...levels.entries()]
      .sort((a, b) => (side === 'bid' ? b[0] - a[0] : a[0] - b[0]))
      .slice(0, n)
      .map(([p, q]) => [p / this.priceScale, q / this.sizeScale]);
  }

  view(nowMs: number, depth = 20): MarketView {
    this.frameTimes = this.frameTimes.filter((t) => nowMs - t < 1000);
    return {
      connected: this.connected,
      seq: this.seq,
      bids: this.synced ? this.top('bid', depth) : [],
      asks: this.synced ? this.top('ask', depth) : [],
      trades: this.trades,
      metrics: this.metricsHistory.at(-1) ?? null,
      metricsHistory: this.metricsHistory,
      framesPerSecond: this.frameTimes.length,
    };
  }
}
