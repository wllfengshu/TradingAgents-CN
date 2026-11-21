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
import RangeUniverseControls, {
  type UniverseMode,
  UNIVERSE_OPTIONS,
  validateRange,
} from '../components/RangeUniverseControls'

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
  const [universeMode, setUniverseMode] = useState<UniverseMode>('full')
  const [strategyId, setStrategyId] = useState(editId)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveFormError, setSaveFormError] = useState('')
  const [evalBusyField, setEvalBusyField] = useState('')
  const [evalTarget, setEvalTarget] = useState<FactorMeta | null>(null)
  const [evalStart, setEvalStart] = useState('2024-01-01')
  const [evalEnd, setEvalEnd] = useState('2024-12-31')
  const [evalUniverse, setEvalUniverse] = useState<UniverseMode>('full')
  const [evalFormError, setEvalFormError] = useState('')

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
          if (bt.universe_mode && UNIVERSE_OPTIONS.some((o) => o.value === bt.universe_mode)) {
            setUniverseMode(bt.universe_mode as UniverseMode)
          } else if (
            s.params?.universe_mode &&
            UNIVERSE_OPTIONS.some((o) => o.value === s.params?.universe_mode)
          ) {
            setUniverseMode(s.params.universe_mode as UniverseMode)
          }
        } else {
          setActive(cloneActive(t.active_factors || emptyActive()))
          setName(String(t.strategy_name || '我的策略'))
          setDescription(String(t.description || ''))
          const bt = t.backtest || {}
          if (bt.start_date) setStart(String(bt.start_date))
          if (bt.end_date) setEnd(String(bt.end_date))
          setUniverseMode('full')
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

  const factorCount = countFactors(active)
  const missingLayers = LAYERS.filter((l) => layerWeightSum(active, l) <= 0)
  const canOpenSave = !busy && factorCount > 0
  const canSubmitSave = canOpenSave && !!name.trim()

  function openSaveDialog() {
    if (factorCount < 1) {
      setError('至少选择一个因子后再保存')
      return
    }
    setSaveFormError('')
    setError('')
    setSaveOpen(true)
  }

  function closeSaveDialog() {
    if (busy) return
    setSaveOpen(false)
    setSaveFormError('')
  }

  async function save(andBacktest: boolean) {
    if (factorCount < 1) {
      setSaveFormError('至少选择一个因子后再保存')
      return
    }
    if (!name.trim()) {
      setSaveFormError('请填写策略名')
      return
    }
    const rangeErr = validateRange(start, end)
    if (andBacktest && rangeErr) {
      setSaveFormError(rangeErr)
      return
    }
    setBusy(true)
    setError('')
    setMsg('')
    setSaveFormError('')
    try {
      const body = {
        name: name.trim(),
        description,
        active_factors: active,
        backtest: { start_date: start, end_date: end, universe_mode: universeMode },
      }
      let id = strategyId
      if (id) await api.updateStrategy(id, body)
      else {
        const created = await api.createStrategy(body)
        id = created.id
        setStrategyId(id)
      }
      setSaveOpen(false)
      if (!andBacktest) {
        nav(`/research/strategies/${id}`)
        return
      }
      const bt = await api.runBacktest(id, {
        start_date: start,
        end_date: end,
        use_precomputed: true,
        universe_mode: universeMode,
      })
      nav(`/research/backtests/${bt.backtest_id}`)
    } catch (e) {
      setSaveFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function openEvalDialog(meta: FactorMeta) {
    setEvalTarget(meta)
    setEvalStart(start)
    setEvalEnd(end)
    setEvalUniverse(universeMode)
    setEvalFormError('')
    setError('')
  }

  function closeEvalDialog() {
    if (evalBusyField) return
    setEvalTarget(null)
    setEvalFormError('')
  }

  async function confirmEval() {
    if (!evalTarget) return
    const rangeErr = validateRange(evalStart, evalEnd)
    if (rangeErr) {
      setEvalFormError(rangeErr)
      return
    }
    setEvalBusyField(evalTarget.field)
    setEvalFormError('')
    setError('')
    setMsg('')
    try {
      const res = await api.triggerData('factor_eval', {
        layer: evalTarget.layer,
        field: evalTarget.field,
        start: evalStart,
        end: evalEnd,
        period: 5,
        plot: true,
        universe_mode: evalUniverse,
      })
      setStart(evalStart)
      setEnd(evalEnd)
      setUniverseMode(evalUniverse)
      const modeLabel = UNIVERSE_OPTIONS.find((o) => o.value === evalUniverse)?.label || evalUniverse
      setMsg(
        `已提交测评 ${evalTarget.name_cn}（${evalTarget.layer}.${evalTarget.field}）· ${evalStart}~${evalEnd} · ${modeLabel} · job ${res.job_id}`,
      )
      setEvalTarget(null)
    } catch (e) {
      setEvalFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setEvalBusyField('')
    }
  }

  return (
    <div className="factors-studio">
      <header className="page-head">
        <div>
          <p className="page-kicker">研究</p>
          <h1 className="page-title">因子库</h1>
          <p className="page-lead">挑选因子、确认组合，再保存为策略。</p>
        </div>
        <div className="factors-hero-actions">
          <Link to="/research/strategies" className="factors-link">
            策略库
          </Link>
          <button type="button" className="primary" disabled={!canOpenSave} onClick={openSaveDialog}>
            保存策略
          </button>
        </div>
      </header>

      {error && <p className="danger">{error}</p>}
      {msg && (
        <p className="ok">
          {msg}{' '}
          <Link to="/ops/jobs" style={{ fontWeight: 600 }}>
            查看任务 →
          </Link>
        </p>
      )}

      <section className="factors-summary" aria-label="已选摘要">
        <div className="factors-summary-top">
          <div>
            <h2>已选摘要</h2>
            <p className="muted">
              共 <strong className="mono">{factorCount}</strong> 个因子
              {missingLayers.length > 0 && (
                <>
                  {' '}
                  · 缺层 {missingLayers.map((l) => LAYER_LABEL[l]).join('、')}
                </>
              )}
            </p>
          </div>
          {factorCount < 1 && <span className="factors-summary-hint">从下方目录勾选开始</span>}
        </div>
        <div className="factors-summary-layers">
          {LAYERS.map((l) => {
            const sum = layerWeightSum(active, l)
            const chips: { key: string; text: string }[] = []
            for (const r of REGIMES) {
              for (const e of active[l]?.[r] || []) {
                chips.push({
                  key: `${l}-${r}-${e.field}`,
                  text: `${REGIME_LABEL[r]} · ${nameByField.get(e.field) || e.field}`,
                })
              }
            }
            return (
              <div key={l} className={`factors-summary-layer${sum <= 0 ? ' is-empty' : ''}`}>
                <div className="factors-summary-layer-head">
                  <span>{LAYER_LABEL[l]}</span>
                  <span className="mono muted">Σ {sum.toFixed(2)}</span>
                </div>
                {chips.length ? (
                  <div className="factors-chip-row">
                    {chips.map((c) => (
                      <span key={c.key} className="factors-chip">
                        {c.text}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="factors-summary-empty">未选</p>
                )}
              </div>
            )
          })}
        </div>
      </section>

      <section className="factors-catalog card">
        <div className="factors-catalog-head">
          <h2>因子目录</h2>
          <p className="muted">按层与风格勾选；行内可改权重、极性或发起单因子测评。</p>
        </div>
        <FactorPicker
          catalog={catalog}
          active={active}
          layer={layer}
          regime={regime}
          onLayer={setLayer}
          onRegime={setRegime}
          onChange={setActive}
          onEval={openEvalDialog}
          evalBusyField={evalBusyField}
        />
      </section>

      {saveOpen && (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeSaveDialog()
          }}
        >
          <div className="modal-panel factors-save-modal" role="dialog" aria-modal="true" aria-labelledby="save-dialog-title">
            <h3 id="save-dialog-title">保存策略</h3>
            <p className="modal-sub">
              当前组合 <span className="mono">{factorCount}</span> 个因子
              {strategyId ? ' · 将更新已有策略' : ' · 新建策略'}
            </p>
            <label className="stack-gap">
              <div className="muted label-xs">策略名</div>
              <input
                className="ctl-sm"
                style={{ width: '100%' }}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </label>
            <label className="stack-gap">
              <div className="muted label-xs">说明</div>
              <textarea
                className="ctl-sm"
                style={{ width: '100%', minHeight: 72, resize: 'vertical' }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <div className="muted label-xs">回测区间与宇宙</div>
            <RangeUniverseControls
              start={start}
              end={end}
              universeMode={universeMode}
              onStart={setStart}
              onEnd={setEnd}
              onUniverseMode={setUniverseMode}
              startLabel="开始日期"
              endLabel="结束日期"
            />
            {saveFormError && (
              <p className="danger" style={{ margin: 0 }}>
                {saveFormError}
              </p>
            )}
            <div className="modal-actions">
              <button type="button" disabled={busy} onClick={closeSaveDialog}>
                取消
              </button>
              <button type="button" disabled={!canSubmitSave || busy} onClick={() => void save(false)}>
                {busy ? '保存中…' : '仅保存'}
              </button>
              <button
                type="button"
                className="primary"
                disabled={!canSubmitSave || busy}
                onClick={() => void save(true)}
              >
                {busy ? '提交中…' : '保存并回测'}
              </button>
            </div>
          </div>
        </div>
      )}

      {evalTarget && (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeEvalDialog()
          }}
        >
          <div className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="eval-dialog-title">
            <h3 id="eval-dialog-title">单因子测评</h3>
            <p className="modal-sub">
              {evalTarget.name_cn}
              <span className="mono">
                {' '}
                · {evalTarget.layer}.{evalTarget.field}
              </span>
            </p>
            <RangeUniverseControls
              start={evalStart}
              end={evalEnd}
              universeMode={evalUniverse}
              onStart={setEvalStart}
              onEnd={setEvalEnd}
              onUniverseMode={setEvalUniverse}
              startLabel="测评开始"
              endLabel="测评结束"
            />
            {evalFormError && (
              <p className="danger" style={{ margin: 0 }}>
                {evalFormError}
              </p>
            )}
            <div className="modal-actions">
              <button type="button" disabled={!!evalBusyField} onClick={closeEvalDialog}>
                取消
              </button>
              <button
                type="button"
                className="primary"
                disabled={!!evalBusyField}
                onClick={() => void confirmEval()}
              >
                {evalBusyField === evalTarget.field ? '提交中…' : '开始测评'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
