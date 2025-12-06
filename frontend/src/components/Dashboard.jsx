import React, { useState } from 'react';
import { useMarketData } from '../hooks/useMarketData';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const SYMBOLS = ['AAPL', 'GOOG', 'MSFT', 'AMZN', 'TSLA'];

const Dashboard = () => {
    const [selectedSymbol, setSelectedSymbol] = useState('AAPL');
    const { data, latestSnapshot, isConnected } = useMarketData(selectedSymbol);

    // Scalability Test Logic
    const runScalabilityTest = () => {
        console.log("Starting scalability test...");
        const connections = [];
        for (let i = 0; i < 1000; i++) {
            // Distribute across symbols
            const sym = SYMBOLS[i % SYMBOLS.length];
            const ws = new WebSocket(`ws://localhost:8000/ws/stream/${sym}`);
            ws.onopen = () => {
                // console.log(`Dummy connection ${i} open`);
            };
            // No-op on message to avoid browser crash, just keeping connection alive
            ws.onmessage = () => {}; 
            connections.push(ws);
        }
        console.log(`Opened ${connections.length} dummy connections.`);
        // Note: Browsers usually limit max connections per domain (approx 6-10).
        // This test might not reach 1000 real concurrent connections in one browser tab 
        // due to browser limits, but demonstrates the logic.
        alert("Scalability test started. Check console. Note: Browser limits may restrict max connections.");
    };

    return (
        <div className="p-6 max-w-7xl mx-auto">
            <header className="mb-8 flex justify-between items-center">
                <h1 className="text-3xl font-bold text-slate-100">Trading Dashboard</h1>
                <div className="flex gap-4">
                    <select 
                        className="bg-slate-800 text-white p-2 rounded border border-slate-700"
                        value={selectedSymbol}
                        onChange={(e) => setSelectedSymbol(e.target.value)}
                    >
                        {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <div className={`flex items-center gap-2 ${isConnected ? 'text-green-500' : 'text-red-500'}`}>
                        <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`}></div>
                        {isConnected ? 'Connected' : 'Disconnected'}
                    </div>
                </div>
            </header>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {/* Metric Cards */}
                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-slate-400 text-sm mb-1">Last Price</h3>
                    <div className="text-2xl font-mono font-bold text-white">
                        ${latestSnapshot?.data.price.toFixed(2) || '---'}
                    </div>
                </div>
                
                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-slate-400 text-sm mb-1">Rolling Volatility (20-tick)</h3>
                    <div className="text-2xl font-mono font-bold text-yellow-400">
                        {latestSnapshot?.analytics.volatility.toFixed(4) || '---'}
                    </div>
                </div>

                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-slate-400 text-sm mb-1">VWAP</h3>
                    <div className="text-2xl font-mono font-bold text-blue-400">
                        ${latestSnapshot?.analytics.vwap.toFixed(2) || '---'}
                    </div>
                </div>

                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700">
                    <h3 className="text-slate-400 text-sm mb-1">Volume</h3>
                    <div className="text-2xl font-mono font-bold text-slate-200">
                        {latestSnapshot?.data.volume || '---'}
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Main Chart */}
                <div className="lg:col-span-2 bg-slate-800 p-4 rounded-xl border border-slate-700 h-[400px]">
                    <h3 className="text-lg font-semibold text-white mb-4">Live VWAP & Price</h3>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                            <XAxis dataKey="time" stroke="#94a3b8" />
                            <YAxis domain={['auto', 'auto']} stroke="#94a3b8" />
                            <Tooltip 
                                contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155' }}
                                itemStyle={{ color: '#e2e8f0' }}
                            />
                            <Line type="monotone" dataKey="price" stroke="#10b981" strokeWidth={2} dot={false} name="Price" />
                            <Line type="monotone" dataKey="vwap" stroke="#3b82f6" strokeWidth={2} dot={false} name="VWAP" />
                        </LineChart>
                    </ResponsiveContainer>
                </div>

                {/* Order Book / L2 Depth */}
                <div className="bg-slate-800 p-4 rounded-xl border border-slate-700 h-[400px] overflow-hidden">
                    <h3 className="text-lg font-semibold text-white mb-4">Level 2 Depth</h3>
                    <div className="flex justify-between text-xs text-slate-400 mb-2 border-b border-slate-700 pb-2">
                        <span>Bid Size</span>
                        <span>Bid Price</span>
                        <span>Ask Price</span>
                        <span>Ask Size</span>
                    </div>
                    <div className="space-y-1 font-mono text-sm">
                        {/* Asks (Reverse Order) */}
                        {latestSnapshot?.data.asks.slice().reverse().map((level, i) => (
                            <div key={`ask-${i}`} className="flex justify-between items-center bg-red-900/20 p-1 rounded">
                                <span className="w-1/4 text-right text-slate-500 opacity-0">-</span>
                                <span className="w-1/4 text-right text-slate-500 opacity-0">-</span>
                                <span className="w-1/4 text-center text-red-400">{level.price.toFixed(2)}</span>
                                <span className="w-1/4 text-right text-slate-300">{level.size}</span>
                            </div>
                        ))}
                        
                        {/* Spread Indicator */}
                        <div className="py-2 text-center text-xs text-slate-500">
                            Spread: {latestSnapshot ? (latestSnapshot.data.ask - latestSnapshot.data.bid).toFixed(2) : '-'}
                        </div>

                        {/* Bids */}
                        {latestSnapshot?.data.bids.map((level, i) => (
                            <div key={`bid-${i}`} className="flex justify-between items-center bg-green-900/20 p-1 rounded">
                                <span className="w-1/4 text-left text-slate-300">{level.size}</span>
                                <span className="w-1/4 text-center text-green-400">{level.price.toFixed(2)}</span>
                                <span className="w-1/4 text-slate-500 opacity-0">-</span>
                                <span className="w-1/4 text-slate-500 opacity-0">-</span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            <div className="mt-8 text-center">
                 <button 
                    onClick={runScalabilityTest}
                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded transition-colors"
                >
                    Run Client-Side Scalability Test (1k Conns)
                </button>
            </div>
        </div>
    );
};

export default Dashboard;

