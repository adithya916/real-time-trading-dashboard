import { describe, expect, it } from 'vitest';
import { splitBatch } from '../worker/wire';

const delimit = (frames: number[][]): Uint8Array => {
  const out: number[] = [];
  for (const f of frames) {
    let n = f.length;
    while (n >= 0x80) {
      out.push((n & 0x7f) | 0x80);
      n >>>= 7;
    }
    out.push(n, ...f);
  }
  return new Uint8Array(out);
};

describe('splitBatch', () => {
  it('splits size-delimited frames in order, including long ones', () => {
    const long = Array.from({ length: 300 }, (_, i) => i % 256);
    const parts = splitBatch(delimit([[1, 2, 3], long, []]));
    expect(parts.map((p) => p.length)).toEqual([3, 300, 0]);
    expect(Array.from(parts[0]!)).toEqual([1, 2, 3]);
    expect(parts[1]![299]).toBe(299 % 256);
  });

  it('rejects a message cut short', () => {
    const data = delimit([[1, 2, 3]]);
    expect(() => splitBatch(data.subarray(0, 2))).toThrow();
  });

  it('returns nothing for an empty message', () => {
    expect(splitBatch(new Uint8Array())).toEqual([]);
  });
});
