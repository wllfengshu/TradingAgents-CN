import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  type FactorEvalDoc,
  type FactorEvalSummary,
  type JobInfo,
  type NightlyStatus,
} from '../api'

function jobDot(status: string) {
  if (status === 'error') return 'bad'
  if (status === 'ok' || status === 'done') return 'ok'
  if (status === 'running' || status === 'queued') return 'warn'
  return 'idle'
}

function fmtCell(v: unknown) {
  if (v == null) return '—'
  if (typeof v === 'number') return Number.isFinite(v) ? v.toFixed(4) : '—'
  return String(v)
}

export default function JobsPage() {
  const [nightly, setNightly] = useState<NightlyStatus | null>(null)
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [evals, setEvals] = useState<FactorEvalSummary[]>([])
  const [evalDetail, setEvalDetail] = useState<FactorEvalDoc | null>(null)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    try {
      setError('')
      const [n, j, e] = await Promise.all([
        api.nightlyStatus(),
        api.listJobs(),
        api.listFactorEvals().catch(() => ({ items: [] as FactorEvalSummary[] })),
      ])
      setNightly(n)
      setJobs(j.items)
      setEvals(e.items)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  async function openEval(id: string) {
    setBusy('eval')
    try {
      setEvalDetail(await api.getFactorEval(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const hasRunningEval = evals.some((e) => e.status === 'running')
  const hasRunningJob = jobs.some((j) => j.status === 'queued' || j.status === 'running')

  useEffect(() => {
    void refresh()
    // 有进行中的测评/任务时加快轮询，便于过程中看到进度
    const ms = hasRunningEval || hasRunningJob ? 2500 : 10000
    const t = setInterval(() => void refresh(), ms)
    return () => clearInterval(t)
  }, [refresh, hasRunningEval, hasRunningJob])

  async function actNightly(action: string) {
    setBusy(action)
    try {
      await api.nightlyAction(action)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  async function trigger(action: string, extra?: Record<string, string | number | boolean>) {
    setBusy(action)
    setMsg('')
    try {
      const res = await api.triggerData(action, extra)
      setMsg(`已提交 ${action} · job ${res.job_id}`)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  const running = jobs.filter((j) => j.status === 'queued' || j.status === 'running').length
  const failed = jobs.filter((j) => j.status === 'error').length
  const nightlyOk = nightly?.status && !/disabled|error|fail/i.test(String(nightly.status))

  return (
    <>
      <div className="page-head">
        <div>
          <p className="page-kicker">运维</p>
          <h1 className="page-title">任务与夜跑</h1>
          <p className="page-lead">管理夜跑调度，触发同步、因子预计算与研究脚本。</p>
        </div>
        <button className="btn-outline" disabled={!!busy} onClick={() => void refresh()}>
          刷新
        </button>
      </div>
      {error && <p className="danger">{error}</p>}
      {msg && <p className="ok">{msg}</p>}

      <div className="stat-row">
        <div className="card soft-card stat-card">
          <div className="label">夜跑状态</div>
          <div className="value" style={{ fontSize: '1.35rem', marginTop: 10 }}>
            <span className={`status-dot ${nightlyOk ? 'ok' : 'warn'}`} style={{ marginRight: 8 }} />
            {nightly?.status || '—'}
          </div>
          <div className="hint">下次 {nightly?.next_run || '—'}</div>
        </div>
        <div className="card soft-card stat-card">
          <div className="label">进行中</div>
          <div className="value">{running}</div>
          <div className="hint">queued / running</div>
        </div>
        <div className="card soft-card stat-card">
          <div className="label">失败任务</div>
          <div className="value">{failed}</div>
          <div className="hint">近期 error</div>
        </div>
      </div>

      <div className="grid-2">
        <section className="card">
          <h2>夜跑（Python 调度）</h2>
          <ul className="bench-list" style={{ marginBottom: '1rem' }}>
            <li>
              <span>上次运行</span>
              <span className="mono muted">{nightly?.last_run || '—'}</span>
            </li>
            <li>
              <span>下次运行</span>
              <span className="mono muted">{nightly?.next_run || '—'}</span>
            </li>
          </ul>
          <div className="row">
            <button className="primary" disabled={!!busy} onClick={() => void actNightly('run')}>
              立即跑夜跑
            </button>
            <button disabled={!!busy} onClick={() => void actNightly('enable')}>
              启用
            </button>
            <button disabled={!!busy} onClick={() => void actNightly('disable')}>
              禁用
            </button>
          </div>
          <p className="muted" style={{ marginTop: '0.85rem', fontSize: '0.85rem', marginBottom: 0 }}>
            stockdb → Mongo 日线/资金流 → 因子预计算；随 API 进程内定时。
          </p>
        </section>

        <section className="card">
          <h2>行情同步</h2>
          <div className="stack">
            <button disabled={!!busy} onClick={() => void trigger('ohlcv')}>
              同步日线
            </button>
            <button disabled={!!busy} onClick={() => void trigger('ohlcv_5m')}>
              同步 5 分钟
            </button>
            <button disabled={!!busy} onClick={() => void trigger('ohlcv_1m')}>
              同步 1 分钟
            </button>
            <button disabled={!!busy} onClick={() => void trigger('capital_flow')}>
              同步资金流
            </button>
          </div>
        </section>
      </div>

      <div className="grid-2" style={{ marginTop: '1rem' }}>
        <section className="card">
          <h2>因子 / 夜跑流水线</h2>
          <div className="stack">
            <button disabled={!!busy} onClick={() => void trigger('precompute')}>
              预计算因子
            </button>
            <button className="primary" disabled={!!busy} onClick={() => void trigger('nightly')}>
              跑完整夜跑流水线
            </button>
          </div>
        </section>

        <section className="card">
          <h2>研究脚本</h2>
          <div className="stack">
            <button
              disabled={!!busy}
              onClick={() => void trigger('factor_eval', { years: String(new Date().getFullYear()) })}
            >
              因子测评（当年）
            </button>
            <button
              disabled={!!busy}
              onClick={() =>
                void trigger('overnight_backtest', {
                  skip_grid: true,
                  skip_precompute: false,
                  skip_eval: false,
                })
              }
            >
              隔夜回测（跳过网格）
            </button>
            <button disabled={!!busy} onClick={() => void trigger('style_detector', { plot: true })}>
              市场风格检测
            </button>
          </div>
          <p className="muted" style={{ marginTop: '0.85rem', fontSize: '0.85rem', marginBottom: 0 }}>
            「因子测评（当年）」为全量；单因子请到 研究 → 因子库 行内「测评」。隔夜回测默认跳过网格；风格检测默认近 30 日并出图。
          </p>
        </section>
      </div>

      <section className="card" style={{ marginTop: '1rem' }}>
        <div className="page-head" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>因子测评结果</h2>
          <button type="button" className="btn-sm" disabled={!!busy} onClick={() => void refresh()}>
            刷新结果
          </button>
        </div>
        <p className="muted" style={{ marginTop: 0, fontSize: '0.85rem' }}>
          任务一开始就会出现在此表；进行中约 2.5s 刷新进度，完成后可查看明细。
        </p>
        {evals.length ? (
          <table>
            <thead>
              <tr>
                <th>状态</th>
                <th>因子</th>
                <th>进度</th>
                <th>区间</th>
                <th>宇宙</th>
                <th>行数</th>
                <th>时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {evals.map((ev) => {
                const st = ev.status || 'ok'
                const stClass =
                  st === 'error' ? 'danger' : st === 'running' ? 'warn' : 'ok'
                return (
                  <tr key={ev.id}>
                    <td>
                      <span className={stClass}>{st === 'running' ? '进行中' : st === 'error' ? '失败' : '完成'}</span>
                    </td>
                    <td>
                      {ev.layer && ev.field ? (
                        <span className="mono">
                          {ev.layer}.{ev.field}
                        </span>
                      ) : (
                        '批量'
                      )}
                    </td>
                    <td style={{ fontSize: '0.82rem' }}>
                      {ev.progress ||
                        (ev.done != null && ev.total != null
                          ? `${ev.done}/${ev.total}`
                          : '—')}
                    </td>
                    <td className="mono">
                      {ev.start || '—'} ~ {ev.end || '—'}
                    </td>
                    <td>{ev.universe_mode || '—'}</td>
                    <td className="mono">{ev.row_count ?? '—'}</td>
                    <td className="mono muted">{ev.updated_at || ev.created_at || ''}</td>
                    <td>
                      <button
                        type="button"
                        className="btn-sm"
                        disabled={busy === 'eval'}
                        onClick={() => void openEval(ev.id)}
                      >
                        {st === 'running' ? '看进度' : '查看'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <p className="muted empty-state">暂无测评结果。请到 研究 → 因子库 行内「测评」提交。</p>
        )}
        {evalDetail && (
          <div className="card soft-card" style={{ marginTop: '0.85rem' }}>
            <div className="page-head" style={{ marginBottom: 8 }}>
              <div>
                <strong>
                  {evalDetail.layer}.{evalDetail.field}
                </strong>
                <span className="muted mono" style={{ marginLeft: 8, fontSize: '0.8rem' }}>
                  {evalDetail.id}
                </span>
              </div>
              <button type="button" className="btn-sm" onClick={() => setEvalDetail(null)}>
                关闭
              </button>
            </div>
            <p className="muted" style={{ fontSize: '0.85rem' }}>
              {evalDetail.start} ~ {evalDetail.end} · {evalDetail.universe_mode} · period=
              {evalDetail.period}
            </p>
            <table>
              <thead>
                <tr>
                  <th>layer</th>
                  <th>field</th>
                  <th>Rank_IC</th>
                  <th>ICIR</th>
                  <th>Score</th>
                  <th>Grade</th>
                </tr>
              </thead>
              <tbody>
                {(evalDetail.rows || []).slice(0, 50).map((r, i) => (
                  <tr key={i}>
                    <td>{fmtCell(r.layer)}</td>
                    <td className="mono">{fmtCell(r.field)}</td>
                    <td className="mono">{fmtCell(r.Rank_IC_Mean)}</td>
                    <td className="mono">{fmtCell(r.Rank_ICIR)}</td>
                    <td className="mono">{fmtCell(r.Total_Score)}</td>
                    <td>{fmtCell(r.Grade)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" style={{ marginTop: '1rem' }}>
        <h2>异步任务</h2>
        <p className="muted" style={{ marginTop: 0, fontSize: '0.85rem' }}>
          任务状态写入 zstock_jobs；测评明细见上方「因子测评结果」。
        </p>
        {jobs.length ? (
          <ul className="job-list">
            {jobs.map((j) => {
              const result = (j.result || {}) as Record<string, unknown>
              const evalId = typeof result.eval_id === 'string' ? result.eval_id : ''
              const backtestId =
                typeof result.backtest_id === 'string' ? result.backtest_id : ''
              const summary = Array.isArray(result.summary) ? result.summary : []
              return (
                <li key={j.id} style={{ alignItems: 'flex-start' }}>
                  <span className={`status-dot ${jobDot(j.status)}`} style={{ marginTop: 6 }} />
                  <div className="job-main">
                    <div className="job-title">
                      {j.kind} · <span className="muted">{j.status}</span>
                      <span className="mono muted" style={{ marginLeft: 8, fontSize: '0.75rem' }}>
                        {j.id}
                      </span>
                    </div>
                    <div className="muted" style={{ fontSize: '0.85rem' }}>
                      {j.message || '—'}
                    </div>
                    {(evalId || backtestId || summary.length > 0) && (
                      <div style={{ marginTop: 6, fontSize: '0.85rem' }}>
                        {evalId && (
                          <div>
                            测评结果已入库{' '}
                            <Link to="/ops/data" style={{ color: 'var(--primary)', fontWeight: 600 }}>
                              zstock_factor_evals
                            </Link>
                            <span className="mono muted"> · {evalId}</span>
                            {result.row_count != null ? ` · ${result.row_count} 行` : ''}
                          </div>
                        )}
                        {backtestId && (
                          <div>
                            回测{' '}
                            <Link
                              to={`/research/backtests/${backtestId}`}
                              style={{ color: 'var(--primary)', fontWeight: 600 }}
                            >
                              {backtestId}
                            </Link>
                          </div>
                        )}
                        {summary.length > 0 && (
                          <pre
                            className="mono"
                            style={{
                              margin: '6px 0 0',
                              fontSize: '0.72rem',
                              maxHeight: 140,
                              overflow: 'auto',
                              background: '#f8fafc',
                              padding: 8,
                              borderRadius: 8,
                            }}
                          >
                            {JSON.stringify(summary.slice(0, 5), null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                    {j.logs?.length ? (
                      <details style={{ marginTop: 6 }}>
                        <summary className="muted" style={{ cursor: 'pointer', fontSize: '0.8rem' }}>
                          日志 {j.logs.length} 条
                        </summary>
                        <pre
                          className="mono"
                          style={{
                            margin: '6px 0 0',
                            fontSize: '0.7rem',
                            maxHeight: 160,
                            overflow: 'auto',
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {j.logs.slice(-30).join('\n')}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                  <div className="mono muted job-time">{j.finished_at || j.created_at}</div>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="muted empty-state">暂无异步任务</p>
        )}
      </section>
    </>
  )
}
