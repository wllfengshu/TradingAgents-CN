import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobInfo } from '../api'

export default function JobToast() {
  const [job, setJob] = useState<JobInfo | null>(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const res = await api.listJobs()
        if (!alive) return
        const running = res.items.find((j) => j.status === 'queued' || j.status === 'running')
        setJob(running || null)
      } catch {
        /* ignore */
      }
      if (alive) setTimeout(() => void tick(), 5000)
    }
    void tick()
    return () => {
      alive = false
    }
  }, [])

  if (!job) return null

  return (
    <div className="job-toast">
      <div className="muted" style={{ fontSize: '0.75rem' }}>
        任务进行中
      </div>
      <div style={{ fontWeight: 600, marginTop: 2 }}>
        {job.kind} · {job.status}
      </div>
      <div className="muted" style={{ fontSize: '0.82rem', marginTop: 4 }}>
        {job.message || '…'}
      </div>
      <Link
        to="/ops/jobs"
        style={{ color: 'var(--primary)', fontSize: '0.85rem', display: 'inline-block', marginTop: 6 }}
      >
        查看任务 →
      </Link>
    </div>
  )
}
