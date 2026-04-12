import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip
} from 'recharts';

function FrequencyTimeChart({ data }) {
  const { times = [], frequencies = [] } = data || {};
  if (!times.length) {
    return <div className="chart-placeholder">No frequency data</div>;
  }
  const arr = times.map((t, i) => ({ time: times[i], frequency: frequencies[i] }));
  return (
    <ResponsiveContainer width="100%" height={200} data-testid="frequency-time-chart">
      <LineChart data={arr} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
        <CartesianGrid stroke="#eee" strokeDasharray="5 5" />
        <XAxis dataKey="time" type="number" domain={[arr[0].time, arr[arr.length - 1].time]} />
        <YAxis dataKey="frequency" />
        <Tooltip />
        <Line type="monotone" dataKey="frequency" stroke="#82ca9d" dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export default FrequencyTimeChart;
