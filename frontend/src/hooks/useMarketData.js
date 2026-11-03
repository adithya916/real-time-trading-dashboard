import { useState, useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../config';

const BATCH_INTERVAL_MS = 100;
const MAX_CHART_POINTS = 100;
const RECONNECT_DELAY_MS = 2000;

export const useMarketData = (symbol) => {
    const [data, setData] = useState([]);
    const [latestSnapshot, setLatestSnapshot] = useState(null);
    const [isConnected, setIsConnected] = useState(false);

    // Newest raw message since the last render tick; older ones are dropped without parsing
    const latestRawRef = useRef(null);

    useEffect(() => {
        if (!symbol) return;

        // Start fresh so the chart never mixes two symbols
        setData([]);
        setLatestSnapshot(null);
        latestRawRef.current = null;

        let ws;
        let reconnectTimer;
        let stopped = false;

        const connect = () => {
            ws = new WebSocket(`${WS_BASE_URL}/ws/stream/${symbol}`);

            ws.onopen = () => setIsConnected(true);

            ws.onmessage = (event) => {
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

            let message;
            try {
                message = JSON.parse(raw);
            } catch (e) {
                console.error("Error parsing message", e);
                return;
            }
            if (message.type !== 'market_data' || message.symbol !== symbol) return;

            setLatestSnapshot(message);
            setData(prev => [...prev, {
                time: new Date(message.processed_at * 1000).toLocaleTimeString(),
                timestamp: message.processed_at,
                vwap: message.analytics.vwap,
                price: message.data.price
            }].slice(-MAX_CHART_POINTS));
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
