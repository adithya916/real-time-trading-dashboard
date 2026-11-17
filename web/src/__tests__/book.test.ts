import { create, toBinary } from '@bufbuild/protobuf';
import { describe, expect, it } from 'vitest';
import { EnvelopeSchema, type Envelope } from '../gen/marketdata/v1/marketdata_pb';
import { MarketState } from '../worker/book';

type Payload = Envelope['payload'];

const bytes = (seq: number, payload: Payload, tsExchNs = 0n) =>
  toBinary(
    EnvelopeSchema,
    create(EnvelopeSchema, {
      venue: 'kraken',
      symbol: 'BTC-USD',
      seq: BigInt(seq),
      tsExchNs,
      payload,
    }),
  );

const level = (price: number, size: number) => ({
  priceTicks: BigInt(price),
  sizeLots: BigInt(size),
});

const snapshot = (seq: number): Uint8Array =>
  bytes(seq, {
    case: 'snapshot',
    value: {
      priceScale: 1,
      sizeScale: 2,
      checksum: 0,
      bids: [level(1000, 150), level(999, 20)],
      asks: [level(1002, 5)],
    },
  } as Payload);

const delta = (seq: number, updates: [side: 1 | 2, price: number, size: number][]): Uint8Array =>
  bytes(seq, {
    case: 'delta',
    value: {
      checksum: 0,
      updates: updates.map(([side, p, q]) => ({
        side,
        priceTicks: BigInt(p),
        sizeLots: BigInt(q),
      })),
    },
  } as Payload);

describe('MarketState', () => {
  it('builds the book from a snapshot in real units, best first', () => {
    const state = new MarketState();
    state.apply(snapshot(7), 0);
    const view = state.view(0);
    expect(view.seq).toBe(7);
    expect(view.bids).toEqual([
      [100.0, 1.5],
      [99.9, 0.2],
    ]);
    expect(view.asks).toEqual([[100.2, 0.05]]);
  });

  it('applies deltas: size 0 deletes, others set the level', () => {
    const state = new MarketState();
    state.apply(snapshot(1), 0);
    state.apply(
      delta(2, [
        [1, 1000, 0],
        [2, 1001, 300],
      ]),
      0,
    );
    const view = state.view(0);
    expect(view.bids).toEqual([[99.9, 0.2]]);
    expect(view.asks[0]).toEqual([100.1, 3]);
    expect(view.seq).toBe(2);
  });

  it('ignores deltas before the first snapshot and after a reset', () => {
    const state = new MarketState();
    state.apply(delta(1, [[1, 1000, 1]]), 0);
    expect(state.view(0).bids).toEqual([]);
    state.apply(snapshot(2), 0);
    state.apply(
      bytes(3, {
        case: 'reset',
        value: { reason: 3, expectedSeq: 0n, receivedSeq: 0n },
      } as Payload),
      0,
    );
    expect(state.view(0).bids).toEqual([]);
  });

  it('keeps trades newest first with the aggressor side', () => {
    const state = new MarketState();
    state.apply(snapshot(1), 0);
    for (const [i, aggressor] of [1, 2].entries()) {
      state.apply(
        bytes(
          2 + i,
          {
            case: 'trade',
            value: { tradeId: `t${i}`, priceTicks: 1001n, sizeLots: 25n, aggressor },
          } as Payload,
          1_700_000_000_000_000_000n,
        ),
        0,
      );
    }
    const [newest, older] = state.view(0).trades;
    expect(newest).toMatchObject({
      id: 't1',
      side: 'sell',
      price: 100.1,
      size: 0.25,
      ts: 1_700_000_000_000,
    });
    expect(older?.side).toBe('buy');
  });

  it('records analytics and counts frames over the last second', () => {
    const state = new MarketState();
    state.apply(
      bytes(1, {
        case: 'analytics',
        value: { mid: 100.1, microprice: 100.12, spreadBps: 1.5 },
      } as Payload),
      100,
    );
    state.apply(snapshot(2), 900);
    const view = state.view(1500);
    expect(view.metrics).toMatchObject({ mid: 100.1, microprice: 100.12, spreadBps: 1.5 });
    expect(view.framesPerSecond).toBe(1); // the frame at 100 ms is over a second old
  });
});
