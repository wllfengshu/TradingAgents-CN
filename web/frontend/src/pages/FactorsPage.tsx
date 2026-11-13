import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ActiveFactors, type FactorMeta } from '../api'
import FactorPicker, {
  LAYERS,
  LAYER_LABEL,
  REGIMES,
  REGIME_LABEL,
  cloneActive,
  countFactors,
  emptyActive,
  layerWeightSum,
} from '../components/FactorPicker'

export default function FactorsPage() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const editId = params.get('id') || ''

  const [catalog, setCatalog] = useState<FactorMeta[]>([])
  const [active, setActive] = useState<ActiveFactors>(emptyActive())
  const [layer, setLayer] = useState<(typeof LAYERS)[number]>('sector')
  const [regime, setRegime] = useState<(typeof REGIMES)[number]>('momentum')
  const [name, setName] = useState('我的策略')
  const [description, setDescription] = useState('')
  const [start, setStart] = useState('2024-01-01')
  const [end, setEnd] = useState('2024-12-31')
  const [strategyId, setStrategyId] = useState(editId)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void (async () => {
      try {
        const [c, t] = await Promise.all([api.factorCatalog(), api.defaultTemplate()])
        setCatalog(c.items)
        if (editId) {
          const s = await api.getStrategy(editId)
          setStrategyId(s.id)
          setName(s.name)
          setDescription(s.description || '')
          setActive(cloneActive(s.params?.active_factors || emptyActive()))
          const bt = (s.params?.backtest || {}) as Record<string, unknown>
          if (bt.start_date) setStart(String(bt.start_date))
          if (bt.end_date) setEnd(String(bt.end_date))
        } else {
          setActive(cloneActive(t.active_factors || emptyActive()))
          setName(String(t.strategy_name || '我的策略'))
          setDescription(String(t.description || ''))
          const bt = t.backtest || {}
          if (bt.start_date) setStart(String(bt.start_date))
          if (bt.end_date) setEnd(String(bt.end_date))
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [editId])

  const nameByField = useMemo(() => {
    const m = new Map<string, string>()
    for (const x of catalog) m.set(x.field, x.name_cn)
    return m
  }, [catalog])

  const missingLayers = LAYERS.filter((l) => layerWeightSum(active, l) <= 0)

  async function save(andBacktest: boolean) {
    setBusy(true)
    setError('')
    try {
      const body = {
        name,
        description,
        active_factors: active,
        backtest: { start_date: start, end_date: end },
      }
      let id = strategyId
      if (id) await api.updateStrategy(id, body)
      else {
        const created = await api.createStrategy(body)
        id = created.id
        setStrategyId(id)
      }
      if (!andBacktest) {
        nav(`/research/strategies/${id}`)
        return
      }
      const bt = await api.runBacktest(id, {
        start_date: start,
        end_date: end,
        use_precomputed: true,
      })
      nav(`/research/backtests/${bt.backtest_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="studio-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">因子库</h1>
          <p className="page-lead">选因子 → 命名 → 保存 → 回测。</p>
        </div>
        <Link to="/research/strategies" style={{ color: 'var(--primary)', fontWeight: 600, fontSize: '0.9rem' }}>
          策略库 →
        </Link>
      </div>
      {error && <p className="danger">{error}</p>}

      <div className="studio-grid">
        <section className="card compact-card">
          <FactorPicker
            catalog={catalog}
            active={active}
            layer={layer}
            regime={regime}
            onLayer={setLayer}
            onRegime={setRegime}
            onChange={setActive}
          />
        </section>

        <section className="card compact-card">
          <h2>已选摘要</h2>
          <p className="muted" style={{ marginTop: 0, fontSize: '0.85rem' }}>
            共 {countFactors(active)} 个因子
          </p>
          {LAYERS.map((l) => {
            const sum = layerWeightSum(active, l)
            return (
              <div key={l} style={{ marginBottom: '0.7rem' }}>
                <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                  {LAYER_LABEL[l]}{' '}
                  <span className="mono muted" style={{ fontWeight: 400, fontSize: '0.78rem' }}>
                    Σw={sum.toFixed(2)}
                  </span>
                </div>
                {REGIMES.map((r) => {
                  const list = active[l]?.[r] || []
                  if (!list.length) return null
                  return (
                    <div key={r} style={{ marginTop: 3, fontSize: '0.82rem' }}>
                      <span className="muted">{REGIME_LABEL[r]} · </span>
                      {list.map((e) => `${nameByField.get(e.field) || e.field}(${e.weight})`).join('、')}
                    </div>
                  )
                })}
                {sum <= 0 && (
                  <div className="warn" style={{ fontSize: '0.78rem' }}>
                    该层尚未选因子
                  </div>
                )}
              </div>
            )
          })}
          {missingLayers.length > 0 && (
            <p className="warn" style={{ fontSize: '0.78rem' }}>
              缺层：{missingLayers.map((l) => LAYER_LABEL[l]).join('、')}
            </p>
          )}
        </section>

        <section className="card compact-card stack">
          <h2>保存与回测</h2>
          <label>
            <div className="muted label-xs">策略名</div>
            <input className="ctl-sm" style={{ width: '100%' }} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            <div className="muted label-xs">说明</div>
            <textarea
              className="ctl-sm"
              style={{ width: '100%', minHeight: 56, resize: 'vertical' }}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <div className="row">
            <label>
              <div className="muted label-xs">回测开始</div>
              <input className="ctl-sm" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              <div className="muted label-xs">回测结束</div>
              <input className="ctl-sm" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>
          <button className="primary btn-sm" disabled={busy || !name.trim()} onClick={() => void save(false)}>
            保存到策略库
          </button>
          <button className="btn-sm" disabled={busy || !name.trim()} onClick={() => void save(true)}>
            保存并回测
          </button>
        </section>
      </div>
    </div>
  )
}
