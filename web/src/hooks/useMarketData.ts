import { useState, useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../config';

const BATCH_INTERVAL_MS = 100;
const MAX_CHART_POINTS = 100;
const RECONNECT_DELAY_MS = 2000;

export interface Level {
  price: number;
  size: number;
}

// The legacy server's JSON message (services/legacy/main_server.py).
export interface LegacyMessage {
  type: 'market_data';
  symbol: string;
  data: {
    symbol: string;
    price: number;
    volume: number;
    timestamp: number;
    bid: number;
    ask: number;
    bids: Level[];
    asks: Level[];
  };
  analytics: { vwap: number; volatility: number };
  processed_at: number;
}

export interface ChartPoint {
  time: string;
  timestamp: number;
  vwap: number;
  price: number;
}

export const useMarketData = (symbol: string) => {
  const [data, setData] = useState<ChartPoint[]>([]);
  const [latestSnapshot, setLatestSnapshot] = useState<LegacyMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  // Newest raw message since the last render tick; older ones are dropped without parsing
  const latestRawRef = useRef<string | null>(null);

  useEffect(() => {
    if (!symbol) return;

    // Start fresh so the chart never mixes two symbols
    setData([]);
    setLatestSnapshot(null);
    latestRawRef.current = null;

    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      ws = new WebSocket(`${WS_BASE_URL}/ws/stream/${symbol}`);

      ws.onopen = () => setIsConnected(true);

      ws.onmessage = (event: MessageEvent<string>) => {
        latestRawRef.current = event.data;
      };

      ws.onclose = () => {
        setIsConnected(false);
        if (!stopped) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    };

    connect();

    // Render at most one update per interval, however fast ticks arrive
    const intervalId = setInterval(() => {
      const raw = latestRawRef.current;
      if (raw === null) return;
      latestRawRef.current = null;

      let message: LegacyMessage;
      try {
        message = JSON.parse(raw) as LegacyMessage;
      } catch (e) {
        console.error('Error parsing message', e);
        return;
      }
      if (message.type !== 'market_data' || message.symbol !== symbol) return;

      setLatestSnapshot(message);
      setData((prev) =>
        [
          ...prev,
          {
            time: new Date(message.processed_at * 1000).toLocaleTimeString(),
            timestamp: message.processed_at,
            vwap: message.analytics.vwap,
            price: message.data.price,
          },
        ].slice(-MAX_CHART_POINTS),
      );
    }, BATCH_INTERVAL_MS);

    return () => {
      stopped = true;
      clearTimeout(reconnectTimer);
      clearInterval(intervalId);
      ws.onmessage = null;
      ws.close();
    };
  }, [symbol]);

  return { data, latestSnapshot, isConnected };
};
