import { useState, useEffect, useRef, useCallback } from 'react';

const BATCH_INTERVAL_MS = 100;

export const useMarketData = (symbol) => {
    const [data, setData] = useState([]);
    const [latestSnapshot, setLatestSnapshot] = useState(null);
    const [isConnected, setIsConnected] = useState(false);
    
    // Buffer for incoming messages
    const bufferRef = useRef([]);
    const wsRef = useRef(null);

    useEffect(() => {
        if (!symbol) return;

        const wsUrl = `ws://localhost:8000/ws/stream/${symbol}`;
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
            console.log(`Connected to ${symbol}`);
            setIsConnected(true);
        };

        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'market_data') {
                    bufferRef.current.push(message);
                }
            } catch (e) {
                console.error("Error parsing message", e);
            }
        };

        ws.onclose = () => {
            console.log(`Disconnected from ${symbol}`);
            setIsConnected(false);
        };

        return () => {
            ws.close();
        };
    }, [symbol]);

    // Batch processing
    useEffect(() => {
        const intervalId = setInterval(() => {
            if (bufferRef.current.length > 0) {
                const newMessages = [...bufferRef.current];
                bufferRef.current = []; // Clear buffer

                // Process batch
                // We append to historical data for charts, and update latest snapshot
                
                // For charts, we might not want to keep ALL ticks if there are thousands.
                // We'll just take the latest one from the batch or all of them depending on chart performance.
                // For 50k ticks/sec (10k per symbol), even 100ms is 1000 ticks.
                // We should probably downsample for visualization.
                // Let's take the last item of the batch for the chart update to be efficient, 
                // or average them.
                
                const lastMsg = newMessages[newMessages.length - 1];
                
                setLatestSnapshot(lastMsg);
                
                // Update history with only the last point to avoid crashing the chart
                setData(prev => {
                    const newData = [...prev, {
                        time: new Date(lastMsg.processed_at * 1000).toLocaleTimeString(),
                        timestamp: lastMsg.processed_at,
                        vwap: lastMsg.analytics.vwap,
                        price: lastMsg.data.price
                    }];
                    // Keep last 100 points
                    if (newData.length > 100) {
                        return newData.slice(newData.length - 100);
                    }
                    return newData;
                });
            }
        }, BATCH_INTERVAL_MS);

        return () => clearInterval(intervalId);
    }, []);

    return { data, latestSnapshot, isConnected };
};

