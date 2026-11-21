import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, type BacktestSummary, type FactorMeta, type StrategyDetail } from '../api'
import { LAYERS, LAYER_LABEL, REGIMES, REGIME_LABEL } from '../components/FactorPicker'
import RangeUniverseControls, {
  type UniverseMode,
  UNIVERSE_OPTIONS,
  validateRange,
} from '../components/RangeUniverseControls'

export default function StrategyDetailPage() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [doc, setDoc] = useState<StrategyDetail | null>(null)
  const [history, setHistory] = useState<BacktestSummary[]>([])
  const [catalog, setCatalog] = useState<FactorMeta[]>([])
  const [start, setStart] = useState('2024-01-01')
  const [end, setEnd] = useState('2024-12-31')
  const [universeMode, setUniverseMode] = useState<UniverseMode>('funnel')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
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
        const bt = (s.params?.backtest || {}) as Record<string, unknown>
        if (bt.start_date) setStart(String(bt.start_date))
        if (bt.end_date) setEnd(String(bt.end_date))
        const mode = (bt.universe_mode || s.params?.universe_mode) as string | undefined
        if (mode && UNIVERSE_OPTIONS.some((o) => o.value === mode)) {
          setUniverseMode(mode as UniverseMode)
        } else {
          setUniverseMode('funnel')
        }
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
  const modeLabel = UNIVERSE_OPTIONS.find((o) => o.value === universeMode)?.label || universeMode

  async function runBacktest() {
    if (!id) return
    const rangeErr = validateRange(start, end)
    if (rangeErr) {
      setError(rangeErr)
      return
    }
    setBusy(true)
    setError('')
    setMsg('')
    try {
      const res = await api.runBacktest(id, {
        start_date: start.trim(),
        end_date: end.trim(),
        use_precomputed: true,
        universe_mode: universeMode,
      })
      setMsg(`已提交回测 · ${start}~${end} · ${modeLabel} · job ${res.job_id}`)
      nav(`/research/backtests/${res.backtest_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="page-kicker">研究 / 策略</p>
          <h1 className="page-title">{doc?.name || '策略'}</h1>
          <p className="page-lead">{doc?.description || '无说明'}</p>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <Link to="/research/strategies" className="factors-link">
            策略库
          </Link>
          <Link to={`/research/factors?id=${id}`}>
            <button className="primary" type="button">
              继续编辑
            </button>
          </Link>
        </div>
      </div>
      {error && <p className="danger">{error}</p>}
      {msg && <p className="ok">{msg}</p>}

      <section className="card stack backtest-setup">
        <h2>回测设置</h2>
        <p className="muted" style={{ margin: 0, fontSize: '0.88rem' }}>
          选择区间与宇宙后发起回测；任务在后台执行，不会卡住页面。
        </p>
        <RangeUniverseControls
          start={start}
          end={end}
          universeMode={universeMode}
          onStart={setStart}
          onEnd={setEnd}
          onUniverseMode={setUniverseMode}
          startLabel="回测开始"
          endLabel="回测结束"
        />
        <div className="row" style={{ gap: 10 }}>
          <button className="primary" type="button" disabled={busy} onClick={() => void runBacktest()}>
            {busy ? '提交中…' : '发起回测'}
          </button>
          <span className="muted" style={{ fontSize: '0.82rem' }}>
            当前：{start} ~ {end} · {modeLabel}
          </span>
        </div>
      </section>

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
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {history.map((b) => (
              <tr key={b.id}>
                <td>
                  <span
                    className={
                      b.status === 'error'
                        ? 'danger'
                        : b.status === 'ok' || b.status === 'done'
                          ? 'ok'
                          : 'warn'
                    }
                  >
                    {b.status}
                  </span>
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
                <td>
                  <Link to={`/research/backtests/${b.id}`} style={{ color: 'var(--primary)' }}>
                    查看结果
                  </Link>
                </td>
              </tr>
            ))}
            {!history.length && (
              <tr>
                <td colSpan={5} className="muted">
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
