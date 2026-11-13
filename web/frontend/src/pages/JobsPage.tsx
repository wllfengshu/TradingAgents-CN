import { useCallback, useEffect, useState } from 'react'
import { api, type JobInfo, type NightlyStatus } from '../api'

function jobDot(status: string) {
  if (status === 'error') return 'bad'
  if (status === 'ok' || status === 'done') return 'ok'
  if (status === 'running' || status === 'queued') return 'warn'
  return 'idle'
}

export default function JobsPage() {
  const [nightly, setNightly] = useState<NightlyStatus | null>(null)
  const [jobs, setJobs] = useState<JobInfo[]>([])
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    try {
      setError('')
      const [n, j] = await Promise.all([api.nightlyStatus(), api.listJobs()])
      setNightly(n)
      setJobs(j.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const t = setInterval(() => void refresh(), 10000)
    return () => clearInterval(t)
  }, [refresh])

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

  async function trigger(action: string) {
    setBusy(action)
    setMsg('')
    try {
      const res = await api.triggerData(action)
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
          <h1 className="page-title">任务与夜跑</h1>
          <p className="page-lead">管理夜跑计划任务，触发同步与因子预计算。</p>
        </div>
        <button disabled={!!busy} onClick={() => void refresh()}>
          刷新
        </button>
      </div>
      {error && <p className="danger">{error}</p>}
      {msg && <p className="ok">{msg}</p>}

      <div className="stat-row">
        <div className="card hoverable stat-card">
          <div className="label">夜跑状态</div>
          <div className="value" style={{ fontSize: '1.35rem', marginTop: 10 }}>
            <span className={`status-dot ${nightlyOk ? 'ok' : 'warn'}`} style={{ marginRight: 8 }} />
            {nightly?.status || '—'}
          </div>
          <div className="hint">下次 {nightly?.next_run || '—'}</div>
        </div>
        <div className="card hoverable stat-card">
          <div className="label">进行中</div>
          <div className="value">{running}</div>
          <div className="hint">queued / running</div>
        </div>
        <div className="card hoverable stat-card">
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
          <h2>数据触发</h2>
          <div className="stack">
            <button disabled={!!busy} onClick={() => void trigger('ohlcv')}>
              同步日线
            </button>
            <button disabled={!!busy} onClick={() => void trigger('capital_flow')}>
              同步资金流
            </button>
            <button disabled={!!busy} onClick={() => void trigger('precompute')}>
              预计算因子
            </button>
            <button className="primary" disabled={!!busy} onClick={() => void trigger('nightly')}>
              跑完整夜跑流水线
            </button>
          </div>
        </section>
      </div>

      <section className="card">
        <h2>异步任务</h2>
        {jobs.length ? (
          <ul className="job-list">
            {jobs.map((j) => (
              <li key={j.id}>
                <span className={`status-dot ${jobDot(j.status)}`} />
                <div className="job-main">
                  <div className="job-title">
                    {j.kind} · <span className="muted">{j.status}</span>
                  </div>
                  <div className="muted" style={{ fontSize: '0.85rem' }}>
                    {j.message || '—'}
                  </div>
                </div>
                <div className="mono muted job-time">{j.created_at}</div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted empty-state">暂无异步任务</p>
        )}
      </section>
    </>
  )
}
