import { useEffect, useState, type MouseEvent } from 'react'
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

function layerBrief(s: StrategySummary): string {
  if (!s.factor_summary) return '—'
  return Object.entries(s.factor_summary)
    .map(([layer, regimes]) => {
      const n = Object.values(regimes).reduce((a, b) => a + Number(b), 0)
      return `${LAYER_LABEL[layer as keyof typeof LAYER_LABEL] || layer} ${n}`
    })
    .join(' · ')
}

export default function StrategiesPage() {
  const [items, setItems] = useState<StrategySummary[]>([])
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')

  useEffect(() => {
    void (async () => {
      try {
        setItems((await api.listStrategies()).items)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [])

  async function onDelete(s: StrategySummary, e: MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (!window.confirm(`确定删除策略「${s.name}」？此操作不可恢复。`)) return
    setBusyId(s.id)
    setError('')
    try {
      await api.deleteStrategy(s.id)
      setItems((prev) => prev.filter((x) => x.id !== s.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId('')
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="page-kicker">研究</p>
          <h1 className="page-title">策略库</h1>
          <p className="page-lead">已保存的因子组合。列表优先，点进详情再回测。</p>
        </div>
        <Link to="/research/factors">
          <button className="primary" type="button">
            新建策略
          </button>
        </Link>
      </div>
      {error && <p className="danger">{error}</p>}

      <div className="data-panel">
        <table className="data-table">
          <thead>
            <tr>
              <th>策略</th>
              <th>因子</th>
              <th>分层</th>
              <th>更新</th>
              <th style={{ width: 88 }} />
            </tr>
          </thead>
          <tbody>
            {items.map((s) => {
              const n = factorCount(s)
              return (
                <tr key={s.id}>
                  <td>
                    <Link className="row-link" to={`/research/strategies/${s.id}`}>
                      {s.name}
                    </Link>
                    <div className="muted" style={{ fontSize: '0.78rem', marginTop: 2 }}>
                      {s.description || '无说明'}
                    </div>
                  </td>
                  <td>
                    <span className={`pill ${n > 0 ? 'ok' : ''}`}>{n} 个</span>
                  </td>
                  <td className="muted" style={{ fontSize: '0.82rem' }}>
                    {layerBrief(s)}
                  </td>
                  <td className="mono muted" style={{ fontSize: '0.78rem' }}>
                    {s.updated_at || '—'}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="btn-sm btn-ghost"
                      disabled={busyId === s.id}
                      onClick={(ev) => void onDelete(s, ev)}
                    >
                      {busyId === s.id ? '删除中…' : '删除'}
                    </button>
                  </td>
                </tr>
              )
            })}
            {!items.length && !error && (
              <tr>
                <td colSpan={5} style={{ padding: '2rem 1rem' }}>
                  <p className="muted" style={{ margin: '0 0 0.75rem' }}>
                    暂无策略，请先在因子库保存。
                  </p>
                  <Link to="/research/factors" className="text-link">
                    打开因子库 →
                  </Link>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
