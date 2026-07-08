import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const palette = ['#0f766e', '#2563eb', '#b45309', '#7c3aed', '#be123c', '#15803d'];

function compactNumber(value) {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
}

function formatBucket(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
  }).format(date);
}

export default function BurnChart({ data, series }) {
  return (
    <section className="chart-panel">
      <div className="section-heading">
        <div>
          <h2>Token burn</h2>
          <p>Cumulative token burn for the selected window.</p>
        </div>
      </div>
      <div className="chart-frame">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 16, right: 16, left: 0, bottom: 8 }}>
            <defs>
              {series.map((name, index) => (
                <linearGradient key={name} id={`fill-${index}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={palette[index % palette.length]} stopOpacity={0.5} />
                  <stop offset="95%" stopColor={palette[index % palette.length]} stopOpacity={0.04} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid stroke="#d8dee4" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="time_bucket"
              tickFormatter={formatBucket}
              tick={{ fill: '#5f6b7a', fontSize: 12 }}
              minTickGap={28}
            />
            <YAxis tickFormatter={compactNumber} tick={{ fill: '#5f6b7a', fontSize: 12 }} width={56} />
            <Tooltip
              labelFormatter={formatBucket}
              formatter={(value) => [compactNumber(value), 'tokens']}
              contentStyle={{ borderRadius: 8, borderColor: '#d8dee4' }}
            />
            <Legend iconType="circle" />
            {series.map((name, index) => (
              <Area
                key={name}
                type="monotone"
                dataKey={name}
                stackId="tokens"
                stroke={palette[index % palette.length]}
                fill={`url(#fill-${index})`}
                strokeWidth={2}
                connectNulls
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
