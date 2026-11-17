import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { marketStore, useMarket } from '../market';

// Temporary: replaced by canvas charts in the charts branch.
export default function PriceChart() {
  useMarket((v) => v.metrics?.ts ?? 0); // only when new analytics arrive (10 Hz)
  const data = marketStore.get().metricsHistory.slice(-300);
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="ts" hide />
          <YAxis domain={['auto', 'auto']} stroke="#94a3b8" width={80} />
          <Tooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155' }} />
          <Line
            type="monotone"
            dataKey="mid"
            stroke="#10b981"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="microprice"
            stroke="#38bdf8"
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="vwapSession"
            stroke="#818cf8"
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
