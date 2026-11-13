import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

type Point = { date: string; equity: number }

export default function EquityChart({ data }: { data: Point[] }) {
  if (!data.length) {
    return <p className="muted">暂无净值数据</p>
  }

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: '#64748b', fontSize: 11 }}
            minTickGap={40}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: '#64748b', fontSize: 11 }}
            domain={['auto', 'auto']}
            width={56}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: '#fff',
              border: '1px solid #e2e8f0',
              borderRadius: 12,
              boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
              fontSize: 12,
            }}
          />
          <Line type="monotone" dataKey="equity" stroke="#2563EB" dot={false} strokeWidth={2.25} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
