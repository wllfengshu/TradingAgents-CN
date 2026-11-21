import { useMemo } from 'react'
import type { ActiveFactors, FactorEntry, FactorMeta } from '../api'

export const LAYERS = ['sector', 'dragon', 'force'] as const
export const REGIMES = ['momentum', 'reversal'] as const

export const LAYER_LABEL: Record<(typeof LAYERS)[number], string> = {
  sector: '板块',
  dragon: '龙头',
  force: '合力',
}

export const REGIME_LABEL: Record<(typeof REGIMES)[number], string> = {
  momentum: '动量',
  reversal: '反转',
}

export function emptyActive(): ActiveFactors {
  return {
    sector: { momentum: [], reversal: [] },
    dragon: { momentum: [], reversal: [] },
    force: { momentum: [], reversal: [] },
  }
}

export function cloneActive(src: ActiveFactors): ActiveFactors {
  const out = emptyActive()
  for (const layer of LAYERS) {
    for (const regime of REGIMES) {
      const list = src?.[layer]?.[regime]
      out[layer]![regime] = Array.isArray(list)
        ? list.map((x) => ({ field: x.field, weight: Number(x.weight), polarity: x.polarity }))
        : []
    }
  }
  if (src.profiles) out.profiles = src.profiles
  return out
}

export function countFactors(active: ActiveFactors): number {
  let n = 0
  for (const layer of LAYERS) {
    for (const regime of REGIMES) {
      n += active[layer]?.[regime]?.length || 0
    }
  }
  return n
}

export function layerWeightSum(active: ActiveFactors, layer: (typeof LAYERS)[number]): number {
  let s = 0
  for (const regime of REGIMES) {
    for (const e of active[layer]?.[regime] || []) s += Number(e.weight) || 0
  }
  return s
}

type Props = {
  catalog: FactorMeta[]
  active: ActiveFactors
  layer: (typeof LAYERS)[number]
  regime: (typeof REGIMES)[number]
  onLayer: (l: (typeof LAYERS)[number]) => void
  onRegime: (r: (typeof REGIMES)[number]) => void
  onChange: (next: ActiveFactors) => void
  /** 单因子测评；不传则不显示「测评」按钮 */
  onEval?: (meta: FactorMeta) => void
  evalBusyField?: string
}

export default function FactorPicker({
  catalog,
  active,
  layer,
  regime,
  onLayer,
  onRegime,
  onChange,
  onEval,
  evalBusyField,
}: Props) {
  const layerCatalog = useMemo(
    () => catalog.filter((x) => x.layer === layer),
    [catalog, layer],
  )

  const selectedMap = useMemo(() => {
    const map = new Map<string, FactorEntry>()
    for (const e of active[layer]?.[regime] || []) map.set(e.field, e)
    return map
  }, [active, layer, regime])

  function toggle(meta: FactorMeta, on: boolean) {
    const next = cloneActive(active)
    const list = [...(next[layer]![regime] || [])]
    const idx = list.findIndex((x) => x.field === meta.field)
    if (on && idx < 0) {
      list.push({
        field: meta.field,
        weight: 0.2,
        polarity: meta.default_polarity || 'positive',
      })
    } else if (!on && idx >= 0) {
      list.splice(idx, 1)
    }
    next[layer]![regime] = list
    onChange(next)
  }

  function patchEntry(field: string, patch: Partial<FactorEntry>) {
    const next = cloneActive(active)
    next[layer]![regime] = (next[layer]![regime] || []).map((e) =>
      e.field === field ? { ...e, ...patch } : e,
    )
    onChange(next)
  }

  return (
    <div className="factor-picker">
      <div className="tabs-layer" role="tablist" aria-label="因子层">
        {LAYERS.map((l) => (
          <button
            key={l}
            type="button"
            role="tab"
            aria-selected={layer === l}
            className={layer === l ? 'active' : ''}
            onClick={() => onLayer(l)}
          >
            {LAYER_LABEL[l]}
          </button>
        ))}
      </div>
      <p className="factor-picker-hint">风格剖面</p>
      <div className="tabs-regime" role="tablist" aria-label="风格剖面">
        {REGIMES.map((r) => (
          <button
            key={r}
            type="button"
            role="tab"
            aria-selected={regime === r}
            className={regime === r ? 'active' : ''}
            onClick={() => onRegime(r)}
          >
            {REGIME_LABEL[r]}
          </button>
        ))}
      </div>

      {layerCatalog.map((meta) => {
        const sel = selectedMap.get(meta.field)
        const on = !!sel
        return (
          <div className={`factor-row${onEval ? ' with-eval' : ''}`} key={meta.field}>
            <input type="checkbox" checked={on} onChange={(e) => toggle(meta, e.target.checked)} />
            <div>
              <div className="name">{meta.name_cn}</div>
              <div className="field">{meta.field}</div>
            </div>
            <input
              type="number"
              step="0.01"
              disabled={!on}
              value={sel?.weight ?? ''}
              onChange={(e) => patchEntry(meta.field, { weight: Number(e.target.value) })}
              title="权重"
            />
            <select
              disabled={!on}
              value={sel?.polarity || meta.default_polarity}
              onChange={(e) => patchEntry(meta.field, { polarity: e.target.value })}
            >
              <option value="positive">正向</option>
              <option value="negative">反向</option>
            </select>
            {onEval && (
              <button
                type="button"
                className="btn-sm"
                disabled={!!evalBusyField}
                title="对该因子跑 IC/分层测评"
                onClick={() => onEval(meta)}
              >
                {evalBusyField === meta.field ? '提交中…' : '测评'}
              </button>
            )}
          </div>
        )
      })}
      {!layerCatalog.length && <p className="muted">该层暂无因子目录</p>}
    </div>
  )
}
