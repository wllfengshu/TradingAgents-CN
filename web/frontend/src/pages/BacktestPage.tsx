import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type BacktestDetail } from '../api'
import EquityChart from '../components/EquityChart'

function pct(v: unknown) {
  if (v == null) return '—'
  return `${(Number(v) * 100).toFixed(2)}%`
}

export default function BacktestPage() {
  const { id = '' } = useParams()
  const [doc, setDoc] = useState<BacktestDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    let alive = true
    const tick = async () => {
      try {
        const d = await api.getBacktest(id)
        if (!alive) return
        setDoc(d)
        setError('')
        if (d.status === 'running' || d.status === 'queued') {
          setTimeout(() => void tick(), 2000)
        }
      } catch (e) {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setTimeout(() => void tick(), 3000)
      }
    }
    void tick()
    return () => {
      alive = false
    }
  }, [id])

  const m = doc?.metrics || {}
  const statusClass =
    doc?.status === 'error' ? 'danger' : doc?.status === 'ok' || doc?.status === 'done' ? 'ok' : 'warn'

  return (
    <>
      <div>
        <h1 className="page-title">回测结果</h1>
        <p className="page-lead">
          状态 <span className={statusClass}>{doc?.status || '加载中'}</span>
          {' · '}
          <Link
            to={doc?.strategy_id ? `/research/strategies/${doc.strategy_id}` : '/research/strategies'}
            style={{ color: 'var(--primary)' }}
          >
            回策略
          </Link>
          {' · '}
          <Link to="/research/factors" style={{ color: 'var(--primary)' }}>
            因子库
          </Link>
        </p>
      </div>
      {error && <p className="danger">{error}</p>}
      {doc?.error && <p className="danger">{doc.error}</p>}

      <div className="stat-row" style={{ gridTemplateColumns: 'repeat(5, minmax(0, 1fr))' }}>
        <div className="card hoverable stat-card">
          <div className="label">累计收益</div>
          <div className="value" style={{ fontSize: '1.65rem' }}>
            {pct(m.total_return)}
          </div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">年化</div>
          <div className="value" style={{ fontSize: '1.65rem' }}>
            {pct(m.annualized_return)}
          </div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">最大回撤</div>
          <div className="value" style={{ fontSize: '1.65rem' }}>
            {pct(m.max_drawdown)}
          </div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">夏普</div>
          <div className="value" style={{ fontSize: '1.65rem' }}>
            {m.sharpe != null ? Number(m.sharpe).toFixed(2) : '—'}
          </div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">区间</div>
          <div className="value" style={{ fontSize: '0.95rem', marginTop: 12 }}>
            {doc?.start_date || '—'}
            <br />~ {doc?.end_date || '—'}
          </div>
        </div>
      </div>

      <section className="card">
        <h2>净值曲线</h2>
        {doc?.equity?.length ? (
          <EquityChart data={doc.equity} />
        ) : (
          <p className="muted">
            {doc?.status === 'running' || doc?.status === 'queued' ? '回测进行中…' : '暂无净值数据'}
          </p>
        )}
      </section>
    </>
  )
}
