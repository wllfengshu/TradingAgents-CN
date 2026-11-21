export type UniverseMode = 'full' | 'prefilter' | 'funnel'

export const UNIVERSE_OPTIONS: { value: UniverseMode; label: string }[] = [
  { value: 'full', label: '全市场' },
  { value: 'prefilter', label: '准入过滤' },
  { value: 'funnel', label: '策略漏斗' },
]

/** 网页回测/测评最长日历跨度，避免一次预加载数年因子把内存打爆。 */
export const MAX_RANGE_DAYS = 366

export function validateRange(start: string, end: string, maxDays = MAX_RANGE_DAYS): string {
  const s = start.trim()
  const e = end.trim()
  if (!s || !e) return '请指定开始与结束日期'
  if (s > e) return '开始日期不能晚于结束日期'
  const t0 = Date.parse(s)
  const t1 = Date.parse(e)
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return '日期格式无效'
  const days = Math.round((t1 - t0) / 86400000)
  if (days > maxDays) return `区间最长 ${maxDays} 天（当前 ${days} 天），请缩短后再试`
  return ''
}

type Props = {
  start: string
  end: string
  universeMode: UniverseMode
  onStart: (v: string) => void
  onEnd: (v: string) => void
  onUniverseMode: (v: UniverseMode) => void
  startLabel?: string
  endLabel?: string
  compact?: boolean
}

export default function RangeUniverseControls({
  start,
  end,
  universeMode,
  onStart,
  onEnd,
  onUniverseMode,
  startLabel = '开始',
  endLabel = '结束',
  compact = false,
}: Props) {
  const ctl = compact ? 'ctl-sm range-ctl' : 'range-ctl'
  return (
    <div
      className="range-universe-controls row"
      style={{ alignItems: 'flex-end', flexWrap: 'wrap', gap: compact ? 10 : 14 }}
    >
      <label className="range-field">
        <div className="muted label-xs">{startLabel}</div>
        <input
          className={ctl}
          type="date"
          value={start}
          onChange={(ev) => onStart(ev.target.value)}
        />
      </label>
      <label className="range-field">
        <div className="muted label-xs">{endLabel}</div>
        <input className={ctl} type="date" value={end} onChange={(ev) => onEnd(ev.target.value)} />
      </label>
      <label className="range-field">
        <div className="muted label-xs">回测宇宙</div>
        <select
          className={ctl}
          value={universeMode}
          onChange={(ev) => onUniverseMode(ev.target.value as UniverseMode)}
        >
          {UNIVERSE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
