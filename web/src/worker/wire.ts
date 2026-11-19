// The gateway sends one WebSocket message per tick holding several envelopes, each prefixed
// with its varint byte length (Protobuf's size-delimited framing; see services/common/
// src/mdcommon/wire.py). This splits a message back into the serialized envelopes, as views
// into the same buffer (no copies).
export function splitBatch(data: Uint8Array): Uint8Array[] {
  const frames: Uint8Array[] = [];
  let i = 0;
  while (i < data.length) {
    let length = 0;
    let shift = 0;
    for (;;) {
      if (i >= data.length) throw new Error('message ends inside a length prefix');
      const byte = data[i++]!;
      length += (byte & 0x7f) * 2 ** shift; // not <<: lengths can pass 2^31 in principle
      if (!(byte & 0x80)) break;
      shift += 7;
    }
    if (i + length > data.length) throw new Error('message ends inside an envelope');
    frames.push(data.subarray(i, i + length));
    i += length;
  }
  return frames;
}
