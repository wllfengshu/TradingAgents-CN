import { useCallback, useEffect, useState } from 'react'
import { api, type SourceStatus } from '../api'

function badgeTone(status: string) {
  if (status === '正常') return 'success'
  if (status === '异常') return 'danger'
  if (status === '未配置') return 'warn'
  return 'neutral'
}

function displayStatus(it: SourceStatus) {
  return it.status || '未知'
}

export default function SourcesPage() {
  const [items, setItems] = useState<SourceStatus[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const refresh = useCallback(async () => {
    try {
      setError('')
      setItems((await api.sourcesStatus()).items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function test(source: 'akshare' | 'qmt_bridge' | 'stockdb' | 'all') {
    setBusy(source)
    setError('')
    try {
      const res = await api.sourcesTest(source)
      const map = new Map(res.items.map((x) => [x.id, x]))
      setItems((prev) => {
        if (source === 'all') return res.items
        return prev.map((p) => map.get(p.id) || p)
      })
      if (source !== 'all' && !items.length) setItems(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
      await refresh()
    }
  }

  const okCount = items.filter((x) => x.status === '正常').length
  const attention = items.length ? items.length - okCount : 0

  return (
    <div className="sources-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">数据源管理</h1>
          <p className="page-lead">查看并测试 akshare / QMT 桥 / stockdb，不写库、不改配置。</p>
        </div>
        <div className="row">
          <button className="primary" disabled={!!busy} onClick={() => void test('all')}>
            {busy === 'all' ? '探测中…' : '全部探测'}
          </button>
          <button className="btn-outline" disabled={!!busy} onClick={() => void refresh()}>
            刷新状态
          </button>
        </div>
      </div>

      {error && <p className="danger">{error}</p>}

      <div className="stat-row">
        <div className="card hoverable soft-card stat-card">
          <div className="label">数据源</div>
          <div className="value hero-num">{items.length || '—'}</div>
          <div className="hint">已注册连接</div>
        </div>
        <div className="card hoverable soft-card stat-card">
          <div className="label">正常</div>
          <div className="value hero-num ok">{items.length ? okCount : '—'}</div>
          <div className="hint">最近探测通过</div>
        </div>
        <div className="card hoverable soft-card stat-card">
          <div className="label">异常 / 未配</div>
          <div className="value hero-num">{items.length ? attention : '—'}</div>
          <div className="hint">需关注</div>
        </div>
      </div>

      <div className="sources-grid">
        {items.map((it) => {
          const status = displayStatus(it)
          return (
            <article key={it.id} className="card hoverable soft-card source-card">
              <header className="source-card-head">
                <div>
                  <h3 className="source-name">{it.name}</h3>
                  <p className="source-id mono">{it.id}</p>
                </div>
                <span className={`badge badge-${badgeTone(status)}`}>{status}</span>
              </header>

              <dl className="source-meta">
                <div>
                  <dt>端点</dt>
                  <dd className="mono">{it.endpoint || '—'}</dd>
                </div>
                <div>
                  <dt>延迟</dt>
                  <dd>{it.latency_ms != null ? `${it.latency_ms} ms` : '—'}</dd>
                </div>
                <div>
                  <dt>最近探测</dt>
                  <dd>{it.checked_at || '尚未探测'}</dd>
                </div>
              </dl>

              {it.message && <p className="source-msg">{it.message}</p>}

              <footer className="source-card-foot">
                <button
                  className="btn-outline"
                  disabled={!!busy}
                  onClick={() => void test(it.id as 'akshare' | 'qmt_bridge' | 'stockdb')}
                >
                  {busy === it.id ? '测试中…' : '测试连接'}
                </button>
              </footer>
            </article>
          )
        })}

        {!items.length && (
          <div className="card soft-card empty-state sources-empty">
            <p className="page-lead" style={{ marginBottom: 16 }}>
              暂无状态数据，点击上方「全部探测」开始检查连接。
            </p>
            <button className="btn-outline" disabled={!!busy} onClick={() => void test('all')}>
              {busy === 'all' ? '探测中…' : '全部探测'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
