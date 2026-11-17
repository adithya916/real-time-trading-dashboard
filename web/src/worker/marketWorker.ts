// Owns the gateway WebSocket. Decodes every binary frame and applies it here, off the main
// thread; the page asks for one MarketView per animation frame instead of handling messages.
import { MarketState } from './book';

export type ToWorker = { type: 'connect'; url: string } | { type: 'frame' };
export type FromWorker = { type: 'view'; view: ReturnType<MarketState['view']> };

const RECONNECT_MS = [500, 1000, 2000, 5000];

let state = new MarketState();
let socket: WebSocket | null = null;
let attempt = 0;
let currentUrl = '';

function connect(url: string): void {
  currentUrl = url;
  socket?.close();
  state = new MarketState();
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    attempt = 0;
    state.connected = true;
  };
  ws.onmessage = (event: MessageEvent<ArrayBuffer>) => {
    state.apply(new Uint8Array(event.data), Date.now());
  };
  ws.onclose = () => {
    state.connected = false;
    if (socket !== ws) return; // replaced by a newer connection
    const delay = RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)];
    attempt += 1;
    setTimeout(() => socket === ws && connect(currentUrl), delay);
  };
  socket = ws;
}

self.onmessage = (event: MessageEvent<ToWorker>) => {
  const message = event.data;
  if (message.type === 'connect') connect(message.url);
  else
    (self as unknown as Worker).postMessage({
      type: 'view',
      view: state.view(Date.now()),
    } satisfies FromWorker);
};
