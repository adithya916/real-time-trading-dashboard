// Where the gateway is. VITE_GATEWAY_URL wins when set. Otherwise a development server talks to
// a local gateway, and a production build talks to the host that served it (Caddy proxies
// /ws, /symbols and /history to the gateway), so one build works on any domain.
const sameOrigin = () =>
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;

export const GATEWAY_URL: string =
  import.meta.env.VITE_GATEWAY_URL ?? (import.meta.env.DEV ? 'ws://localhost:8080' : sameOrigin());
export const DEFAULT_VENUE = import.meta.env.VITE_DEFAULT_VENUE ?? 'kraken';
export const DEFAULT_SYMBOL = import.meta.env.VITE_DEFAULT_SYMBOL ?? 'BTC-USD';

export const streamUrl = (venue: string, symbol: string) =>
  `${GATEWAY_URL}/ws/${encodeURIComponent(venue)}/${encodeURIComponent(symbol)}`;

// The same host over HTTP, for /symbols and /history.
export const GATEWAY_HTTP_URL = GATEWAY_URL.replace(/^ws/, 'http');
