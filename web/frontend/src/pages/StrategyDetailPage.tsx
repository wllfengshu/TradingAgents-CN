import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type BacktestSummary, type FactorMeta, type StrategyDetail } from '../api'
import { LAYERS, LAYER_LABEL, REGIMES, REGIME_LABEL } from '../components/FactorPicker'

export default function StrategyDetailPage() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [doc, setDoc] = useState<StrategyDetail | null>(null)
  const [history, setHistory] = useState<BacktestSummary[]>([])
  const [catalog, setCatalog] = useState<FactorMeta[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!id) return
    void (async () => {
      try {
        const [s, bts, c] = await Promise.all([
          api.getStrategy(id),
          api.listBacktests(id),
          api.factorCatalog(),
        ])
        setDoc(s)
        setHistory(bts.items)
        setCatalog(c.items)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [id])

  const nameByField = useMemo(() => {
    const m = new Map<string, string>()
    for (const x of catalog) m.set(x.field, x.name_cn)
    return m
  }, [catalog])

  const af = doc?.params?.active_factors

  async function runBacktest() {
    if (!id) return
    setBusy(true)
    setError('')
    try {
      const btCfg = (doc?.params?.backtest || {}) as Record<string, unknown>
      const res = await api.runBacktest(id, {
        start_date: btCfg.start_date ? String(btCfg.start_date) : undefined,
        end_date: btCfg.end_date ? String(btCfg.end_date) : undefined,
        use_precomputed: true,
      })
      nav(`/research/backtests/${res.backtest_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">{doc?.name || '策略'}</h1>
          <p className="page-lead">{doc?.description || '无说明'}</p>
        </div>
        <div className="row">
          <Link to={`/research/factors?id=${id}`}>
            <button className="primary" type="button">
              继续编辑
            </button>
          </Link>
          <button type="button" disabled={busy} onClick={() => void runBacktest()}>
            发起回测
          </button>
          <Link to="/research/strategies" className="muted">
            ← 策略库
          </Link>
        </div>
      </div>
      {error && <p className="danger">{error}</p>}

      <section className="card">
        <h2>因子组合</h2>
        {LAYERS.map((layer) => {
          const has = REGIMES.some((r) => (af?.[layer]?.[r] || []).length > 0)
          if (!has) return null
          return (
            <div key={layer} style={{ marginBottom: '1rem' }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{LAYER_LABEL[layer]}</div>
              {REGIMES.map((r) => {
                const list = af?.[layer]?.[r] || []
                if (!list.length) return null
                return (
                  <div key={r} style={{ fontSize: '0.95rem', marginBottom: 4 }}>
                    <span className="muted">{REGIME_LABEL[r]}：</span>
                    {list
                      .map(
                        (e) =>
                          `${nameByField.get(e.field) || e.field} · w=${e.weight} · ${
                            e.polarity === 'negative' ? '反向' : '正向'
                          }`,
                      )
                      .join('；')}
                  </div>
                )
              })}
            </div>
          )
        })}
        {!af && <p className="muted">无因子数据</p>}
      </section>

      <section className="card">
        <h2>历史回测</h2>
        <table>
          <thead>
            <tr>
              <th>状态</th>
              <th>区间</th>
              <th>收益</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {history.map((b) => (
              <tr key={b.id}>
                <td>
                  <Link to={`/research/backtests/${b.id}`} style={{ color: 'var(--primary)' }}>
                    {b.status}
                  </Link>
                </td>
                <td className="mono">
                  {b.start_date} ~ {b.end_date}
                </td>
                <td className="mono">
                  {b.metrics?.total_return != null
                    ? `${(Number(b.metrics.total_return) * 100).toFixed(2)}%`
                    : '—'}
                </td>
                <td className="mono muted">{b.created_at || ''}</td>
              </tr>
            ))}
            {!history.length && (
              <tr>
                <td colSpan={4} className="muted">
                  暂无回测记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  )
}
