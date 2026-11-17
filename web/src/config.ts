export const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL ?? 'ws://localhost:8080';
export const DEFAULT_VENUE = import.meta.env.VITE_DEFAULT_VENUE ?? 'kraken';
export const DEFAULT_SYMBOL = import.meta.env.VITE_DEFAULT_SYMBOL ?? 'BTC-USD';

export const streamUrl = (venue: string, symbol: string) =>
  `${GATEWAY_URL}/ws/${encodeURIComponent(venue)}/${encodeURIComponent(symbol)}`;
