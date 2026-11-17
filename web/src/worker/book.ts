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

// One-second OHLCV built from trades; time is Unix seconds.
export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// The top of the book at one moment, for the depth heatmap.
export interface DepthColumn {
  t: number; // ms
  mid: number;
  bids: Level[];
  asks: Level[];
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
  candles: Candle[]; // oldest first
  depthColumn: DepthColumn | null; // newest only; the heatmap keeps its own history
  framesPerSecond: number;
}

const TRADES_KEPT = 60;
const METRICS_KEPT = 600; // 60 s at 10 Hz
const CANDLES_KEPT = 900; // 15 minutes of one-second candles
const COLUMN_EVERY_MS = 250;
const COLUMN_LEVELS = 25;

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
  candles: Candle[] = [];
  private depthColumn: DepthColumn | null = null;
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
        this.addToCandle(this.trades[0]!);
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

  private addToCandle(trade: TradePrint): void {
    const time = Math.floor(trade.ts / 1000);
    const last = this.candles.at(-1);
    if (last && last.time === time) {
      last.high = Math.max(last.high, trade.price);
      last.low = Math.min(last.low, trade.price);
      last.close = trade.price;
      last.volume += trade.size;
    } else if (!last || time > last.time) {
      const p = trade.price;
      this.candles.push({ time, open: p, high: p, low: p, close: p, volume: trade.size });
      if (this.candles.length > CANDLES_KEPT) this.candles.shift();
    }
    // A trade older than the last candle (out-of-order venue timestamp) is left out.
  }

  top(side: 'bid' | 'ask', n: number): Level[] {
    const levels = side === 'bid' ? this.bids : this.asks;
    return [...levels.entries()]
      .sort((a, b) => (side === 'bid' ? b[0] - a[0] : a[0] - b[0]))
      .slice(0, n)
      .map(([p, q]) => [p / this.priceScale, q / this.sizeScale]);
  }

  private captureColumn(nowMs: number): void {
    if (!this.synced || (this.depthColumn && nowMs - this.depthColumn.t < COLUMN_EVERY_MS)) return;
    const bids = this.top('bid', COLUMN_LEVELS);
    const asks = this.top('ask', COLUMN_LEVELS);
    if (!bids[0] || !asks[0]) return;
    this.depthColumn = { t: nowMs, mid: (bids[0][0] + asks[0][0]) / 2, bids, asks };
  }

  view(nowMs: number, depth = 20): MarketView {
    this.captureColumn(nowMs);
    this.frameTimes = this.frameTimes.filter((t) => nowMs - t < 1000);
    return {
      connected: this.connected,
      seq: this.seq,
      bids: this.synced ? this.top('bid', depth) : [],
      asks: this.synced ? this.top('ask', depth) : [],
      trades: this.trades,
      metrics: this.metricsHistory.at(-1) ?? null,
      metricsHistory: this.metricsHistory,
      candles: this.candles,
      depthColumn: this.depthColumn,
      framesPerSecond: this.frameTimes.length,
    };
  }
}
