import { useEffect, useState } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Brush
} from 'recharts';

function PowerTimeChart({ freq }) {
  const [data, setData] = useState([]);
  const [range, setRange] = useState({ startIndex: 0, endIndex: 0 });

  useEffect(() => {
    async function fetchPower() {
      try {
        const res = await fetch(`/api/signals/${freq}/power`);
        if (!res.ok) return;
        const j = await res.json();
        const arr = j.times.map((t, i) => ({ time: j.times[i], power: j.powers[i] }));
        setData(arr);
        setRange({ startIndex: 0, endIndex: arr.length - 1 });
      } catch (err) {
        console.error('Failed to fetch power data', err);
      }
    }
    if (freq) fetchPower();
  }, [freq]);

  if (!data.length) return <div className="chart-placeholder">No power data</div>;

  const visible = data.slice(range.startIndex, range.endIndex + 1);

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={visible} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
        <CartesianGrid stroke="#eee" strokeDasharray="5 5" />
        <XAxis dataKey="time" type="number" domain={[visible[0].time, visible[visible.length - 1].time]} />
        <YAxis dataKey="power" />
        <Tooltip />
        <Line type="monotone" dataKey="power" stroke="#8884d8" dot={false} />
        <Brush dataKey="time" startIndex={range.startIndex} endIndex={range.endIndex} onChange={(r) => setRange(r)} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export default PowerTimeChart;
