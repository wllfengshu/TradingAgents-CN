import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type WorkbenchSummary } from '../api'

function fmtPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return ''
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function shortName(name: string) {
  return name.replace(/^zstock_/, '').replace(/_/g, ' ')
}

export default function WorkbenchPage() {
  const [data, setData] = useState<WorkbenchSummary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const summary = await api.workbenchSummary()
        if (!alive) return
        setData(summary)
        setError('')
      } catch (e) {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const latestBt = data?.recent_backtests?.[0]
  const timeline: { t: string; text: string; href?: string }[] = []
  if (data?.nightly?.last_run) {
    timeline.push({
      t: String(data.nightly.last_run),
      text: `夜跑 · ${data.nightly.status || ''}`,
      href: '/ops/jobs',
    })
  }
  if (latestBt) {
    timeline.push({
      t: String(latestBt.created_at || ''),
      text: `回测 ${latestBt.status}${
        latestBt.metrics?.total_return != null
          ? ` · ${(Number(latestBt.metrics.total_return) * 100).toFixed(1)}%`
          : ''
      }`,
      href: `/research/backtests/${latestBt.id}`,
    })
  }
  for (const j of data?.recent_jobs || []) {
    if (j.status === 'queued' || j.status === 'running') {
      timeline.push({ t: j.created_at, text: `${j.kind} · ${j.status}`, href: '/ops/jobs' })
    }
  }
  if (!timeline.length && data) timeline.push({ t: '—', text: '暂无近期活动' })

  const index = data?.market?.index
  const gold = data?.market?.gold
  const news = data?.news || []
  const nightlyOk = data?.nightly?.status && !/disabled|error|fail/i.test(String(data.nightly.status))
  const strategyCount = data?.recent_strategies?.length ?? 0
  const backtestCount = data?.recent_backtests?.length ?? 0

  const chartData = useMemo(() => {
    const prefer = [
      'zstock_ohlcv',
      'zstock_capital_flow',
      'zstock_factor_market',
      'zstock_factor_sector',
      'zstock_factor_dragon',
      'zstock_factor_force',
    ]
    const map = new Map((data?.data_health || []).map((x) => [x.collection, x.count]))
    return prefer.map((name) => ({
      name: shortName(name),
      count: map.get(name) ?? 0,
    }))
  }, [data])

  const modules = [
    {
      title: '因子库',
      desc: '编排因子组合并保存策略',
      href: '/research/factors',
      status: 'ok' as const,
    },
    {
      title: '策略库',
      desc: `${strategyCount} 条近期策略`,
      href: '/research/strategies',
      status: strategyCount > 0 ? ('ok' as const) : ('idle' as const),
    },
    {
      title: '数据源',
      desc: 'akshare / QMT 桥 / stockdb',
      href: '/ops/sources',
      status: 'warn' as const,
    },
    {
      title: '数据管理',
      desc: 'Mongo 只读浏览',
      href: '/ops/data',
      status: 'ok' as const,
    },
    {
      title: '任务与夜跑',
      desc: data?.nightly?.status ? `夜跑 ${data.nightly.status}` : 'Python 调度',
      href: '/ops/jobs',
      status: nightlyOk ? ('ok' as const) : ('warn' as const),
    },
    {
      title: '交易订单',
      desc: `${data?.order_count ?? 0} 条订单`,
      href: '/trade/orders',
      status: (data?.order_count ?? 0) > 0 ? ('ok' as const) : ('idle' as const),
    },
  ]

  const today = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  })

  return (
    <>
      <div className="welcome">
        <div>
          <h1>欢迎回来</h1>
          <p>{error || data?.headline || '加载今日状态…'}</p>
        </div>
        <div className="muted" style={{ fontSize: '0.9rem' }}>
          {today}
        </div>
      </div>

      <div className="stat-row">
        <div className="card hoverable stat-card">
          <div className="label">进行中任务</div>
          <div className="value">{data?.running_jobs ?? '—'}</div>
          <div className="hint">调用 / 异步队列</div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">订单数</div>
          <div className="value">{data?.order_count ?? '—'}</div>
          <div className="hint">成交与委托总量</div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">近期回测</div>
          <div className="value">{backtestCount || '—'}</div>
          <div className="hint">研究产出</div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h2>数据体量</h2>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis
                  tick={{ fill: '#64748b', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={56}
                  tickFormatter={(v) =>
                    v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : String(v)
                  }
                />
                <Tooltip
                  contentStyle={{
                    borderRadius: 12,
                    border: '1px solid #e2e8f0',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
                  }}
                  formatter={(value) => [Number(value).toLocaleString(), '文档数']}
                />
                <Bar dataKey="count" fill="#2563EB" radius={[6, 6, 0, 0]} maxBarSize={42} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card">
          <h2>行情与状态</h2>
          <div className="quote-bar">
            <div className="quote-item">
              <div className="name">{index?.name || '大盘'}</div>
              <div className="price mono">
                {index?.price != null ? index.price.toLocaleString() : '—'}
                {index?.change_pct != null && (
                  <span
                    className={index.change_pct >= 0 ? 'ok' : 'danger'}
                    style={{ marginLeft: 8, fontSize: '0.95rem' }}
                  >
                    {fmtPct(index.change_pct)}
                  </span>
                )}
              </div>
            </div>
            <div className="quote-item">
              <div className="name">{gold?.name || '国际金价'}</div>
              <div className="price mono">
                {gold?.price != null ? gold.price.toLocaleString() : '—'}
              </div>
            </div>
          </div>
          <ul className="bench-list" style={{ marginTop: 8 }}>
            <li>
              <span>夜跑调度</span>
              <span className="mono muted">{data?.nightly?.status || '—'}</span>
            </li>
            <li>
              <span>下次运行</span>
              <span className="mono muted">{data?.nightly?.next_run || '—'}</span>
            </li>
            <li>
              <span>行情日期</span>
              <span className="mono muted">{data?.ohlcv_latest || '—'}</span>
            </li>
          </ul>
        </div>
      </div>

      <div>
        <h2 className="page-title" style={{ fontSize: '1.15rem', marginBottom: '1rem' }}>
          模块
        </h2>
        <div className="grid-modules">
          {modules.map((m) => (
            <Link key={m.href} to={m.href} className="card hoverable module-card">
              <div className="top">
                <div className="title">{m.title}</div>
                <span className={`status-dot ${m.status === 'ok' ? 'ok' : m.status === 'warn' ? 'warn' : 'idle'}`} />
              </div>
              <p className="desc">{m.desc}</p>
            </Link>
          ))}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h2>活动时间线</h2>
          <ul className="timeline">
            {timeline.map((item, i) => (
              <li key={i}>
                <div className="t">{item.t || '—'}</div>
                {item.href ? (
                  <Link to={item.href} style={{ color: 'var(--primary)', fontWeight: 500 }}>
                    {item.text}
                  </Link>
                ) : (
                  <div>{item.text}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
        <div className="card">
          <h2>AI 资讯</h2>
          {news.length ? (
            <ul className="news-list">
              {news.map((n, i) => (
                <li key={i}>
                  <div style={{ fontWeight: 600 }}>{n.title}</div>
                  {n.summary && (
                    <div className="muted" style={{ fontSize: '0.9rem', marginTop: 4 }}>
                      {n.summary}
                    </div>
                  )}
                  {n.url && (
                    <a
                      href={n.url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: 'var(--primary)', fontSize: '0.85rem' }}
                    >
                      原文 →
                    </a>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">暂无 AI 汇总资讯</p>
          )}
        </div>
      </div>
    </>
  )
}
