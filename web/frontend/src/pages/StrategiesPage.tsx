import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type StrategySummary } from '../api'
import { LAYER_LABEL } from '../components/FactorPicker'

function factorCount(s: StrategySummary): number {
  let n = 0
  for (const regimes of Object.values(s.factor_summary || {})) {
    for (const c of Object.values(regimes)) n += Number(c) || 0
  }
  return n
}

export default function StrategiesPage() {
  const [items, setItems] = useState<StrategySummary[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    void (async () => {
      try {
        setItems((await api.listStrategies()).items)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [])

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">策略库</h1>
          <p className="page-lead">已保存的因子组合，点击进入详情或回测。</p>
        </div>
        <Link to="/research/factors">
          <button className="primary" type="button">
            新建策略
          </button>
        </Link>
      </div>
      {error && <p className="danger">{error}</p>}

      <div className="grid-modules">
        {items.map((s) => (
          <Link key={s.id} to={`/research/strategies/${s.id}`} className="card hoverable module-card">
            <div className="top">
              <div className="title">{s.name}</div>
              <span className={`status-dot ${factorCount(s) > 0 ? 'ok' : 'idle'}`} />
            </div>
            <p className="desc">{s.description || '无说明'}</p>
            <div className="row muted" style={{ marginTop: 12, fontSize: '0.82rem' }}>
              <span>{factorCount(s)} 个因子</span>
              {s.factor_summary &&
                Object.entries(s.factor_summary).map(([layer, regimes]) => (
                  <span key={layer}>
                    {LAYER_LABEL[layer as keyof typeof LAYER_LABEL] || layer}:
                    {Object.values(regimes).reduce((a, b) => a + Number(b), 0)}
                  </span>
                ))}
            </div>
            <div className="mono muted" style={{ marginTop: 8, fontSize: '0.75rem' }}>
              {s.updated_at || ''}
            </div>
          </Link>
        ))}
        {!items.length && !error && (
          <div className="card empty-state" style={{ gridColumn: '1 / -1' }}>
            <p className="muted">暂无策略，请先在因子库保存。</p>
            <Link to="/research/factors" style={{ color: 'var(--primary)', fontWeight: 600 }}>
              打开因子库 →
            </Link>
          </div>
        )}
      </div>
    </>
  )
}
