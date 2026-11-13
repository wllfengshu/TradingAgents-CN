import { useCallback, useEffect, useState } from 'react'
import { api, type MongoCollection, type MongoStatus } from '../api'
import { loadMongoBrowseCache, saveMongoBrowseCache } from '../mongoBrowseCache'

export default function DataBrowsePage() {
  const cached = loadMongoBrowseCache()
  const [status, setStatus] = useState<MongoStatus | null>(cached.status)
  const [collections, setCollections] = useState<MongoCollection[]>(cached.collections)
  const [selected, setSelected] = useState(cached.selected)
  const [code, setCode] = useState(cached.code)
  const [start, setStart] = useState(cached.start)
  const [end, setEnd] = useState(cached.end)
  const [advanced, setAdvanced] = useState(cached.advanced)
  const [filterJson, setFilterJson] = useState(cached.filterJson)
  const [page, setPage] = useState(cached.page)
  const [rows, setRows] = useState<Record<string, unknown>[]>(cached.rows)
  const [fields, setFields] = useState<string[]>(cached.fields)
  const [total, setTotal] = useState(cached.total)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState('')
  const [metaBusy, setMetaBusy] = useState(false)

  const persist = useCallback((patch: Parameters<typeof saveMongoBrowseCache>[0]) => {
    saveMongoBrowseCache(patch)
  }, [])

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.mongoStatus()
      setStatus(s)
      persist({ status: s })
      return s
    } catch (e) {
      const s: MongoStatus = {
        ok: false,
        host: '—',
        port: 0,
        database: '—',
        message: e instanceof Error ? e.message : String(e),
      }
      setStatus(s)
      persist({ status: s })
      return s
    }
  }, [persist])

  const loadCollections = useCallback(async () => {
    try {
      setError('')
      const cols = await api.mongoCollections()
      setCollections(cols.items)
      setSelected((prev) => {
        const next = prev || cols.items[0]?.name || ''
        persist({ collections: cols.items, selected: next })
        return next
      })
      return cols.items
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      // keep previous collections in UI + cache
      return null
    }
  }, [persist])

  useEffect(() => {
    let alive = true
    void (async () => {
      setMetaBusy(true)
      try {
        await loadStatus()
        if (!alive) return
        // only refetch collections if cache empty
        if (!loadMongoBrowseCache().collections.length) {
          await loadCollections()
        }
      } finally {
        if (alive) setMetaBusy(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [loadStatus, loadCollections])

  async function testMongo() {
    setMetaBusy(true)
    try {
      await loadStatus()
    } finally {
      setMetaBusy(false)
    }
  }

  async function loadMeta() {
    setMetaBusy(true)
    try {
      await Promise.all([loadStatus(), loadCollections()])
    } finally {
      setMetaBusy(false)
    }
  }

  async function query(nextPage = page, col = selected) {
    if (!col) return
    setBusy(true)
    setError('')
    try {
      const res = await api.mongoQuery({
        collection: col,
        code: code || undefined,
        start: start || undefined,
        end: end || undefined,
        filter: filterJson.trim() || undefined,
        page: nextPage,
        page_size: 50,
      })
      setRows(res.items)
      setFields(res.fields)
      setTotal(res.total_estimate)
      setPage(res.page)
      persist({
        selected: col,
        code,
        start,
        end,
        filterJson,
        page: res.page,
        rows: res.items,
        fields: res.fields,
        total: res.total_estimate,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!selected) return
    const c = loadMongoBrowseCache()
    // 切回本页时：同一集合且已有结果则复用缓存
    if (c.selected === selected && c.rows.length > 0) {
      setRows(c.rows)
      setFields(c.fields)
      setTotal(c.total)
      setPage(c.page || 1)
      return
    }
    void query(1, selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const displayFields = fields.filter((f) => f !== '_id').slice(0, 8)

  return (
    <div className="data-browse">
      <div className="page-head">
        <div>
          <h1 className="page-title">数据管理</h1>
          <p className="page-lead">只读浏览 Mongo 集合与文档。</p>
        </div>
        <div className="row">
          <button type="button" className="btn-sm" disabled={metaBusy} onClick={() => void testMongo()}>
            测试 Mongo
          </button>
          <button type="button" className="btn-sm" disabled={metaBusy} onClick={() => void loadMeta()}>
            刷新集合
          </button>
        </div>
      </div>
      {error && <p className="danger">{error}</p>}

      <div className="card compact-card mongo-status-bar">
        <div className="row" style={{ gap: '0.65rem' }}>
          <span className={`status-dot ${status == null ? 'idle' : status.ok ? 'ok' : 'bad'}`} />
          <div>
            <div className="muted label-xs">Mongo 连接</div>
            <div className="status-line">
              <span className={status == null ? 'muted' : status.ok ? 'ok' : 'danger'}>
                {status == null ? '检测中…' : status.ok ? '正常' : '异常'}
              </span>
              <span className="mono muted">
                {status ? `${status.host}:${status.port}/${status.database}` : '—'}
              </span>
              {status?.message && <span className="muted">· {status.message}</span>}
            </div>
          </div>
        </div>
        <div className="muted label-xs">{collections.length} 个集合</div>
      </div>

      <div className="data-browse-layout">
        <aside className="card compact-card collection-panel">
          <h2>集合目录</h2>
          <div className="collection-list">
            {collections.map((c) => (
              <button
                key={c.name}
                type="button"
                className={selected === c.name ? 'active' : ''}
                onClick={() => {
                  if (c.name === selected) return
                  setSelected(c.name)
                  setPage(1)
                  setRows([])
                  setFields([])
                  setTotal(0)
                  persist({ selected: c.name, page: 1, rows: [], fields: [], total: 0 })
                }}
              >
                <div className="col-name">{c.name}</div>
                <div className="muted col-meta">
                  {c.count.toLocaleString()} 条 · {c.latest_trade_date || '—'}
                </div>
              </button>
            ))}
            {!collections.length && (
              <p className="muted" style={{ margin: 0, fontSize: '0.85rem' }}>
                {metaBusy ? '加载中…' : '暂无集合，点「刷新集合」'}
              </p>
            )}
          </div>
        </aside>

        <section className="card compact-card stack query-panel">
          <h2>
            文档查询 <span className="muted" style={{ fontWeight: 500 }}>· {selected || '未选择'}</span>
          </h2>
          <div className="row filter-row">
            <input
              className="ctl-sm"
              placeholder="代码"
              value={code}
              onChange={(e) => {
                setCode(e.target.value)
                persist({ code: e.target.value })
              }}
            />
            <input
              className="ctl-sm"
              placeholder="开始"
              value={start}
              onChange={(e) => {
                setStart(e.target.value)
                persist({ start: e.target.value })
              }}
            />
            <input
              className="ctl-sm"
              placeholder="结束"
              value={end}
              onChange={(e) => {
                setEnd(e.target.value)
                persist({ end: e.target.value })
              }}
            />
            <button className="primary btn-sm" disabled={busy || !selected} onClick={() => void query(1)}>
              查询
            </button>
          </div>
          <button
            type="button"
            className="btn-sm btn-ghost"
            onClick={() => {
              setAdvanced((v) => {
                persist({ advanced: !v })
                return !v
              })
            }}
          >
            {advanced ? '收起高级条件' : '高级 JSON filter'}
          </button>
          {advanced && (
            <textarea
              className="ctl-sm"
              style={{ width: '100%', minHeight: 64, resize: 'vertical' }}
              placeholder='例如 {"period":"day"}'
              value={filterJson}
              onChange={(e) => {
                setFilterJson(e.target.value)
                persist({ filterJson: e.target.value })
              }}
            />
          )}

          <div className="muted label-xs">
            本页 {rows.length} · 估计 {total.toLocaleString()} · 第 {page} 页
          </div>

          <div className="table-scroll">
            <table className="table-dense">
              <thead>
                <tr>
                  {displayFields.map((f) => (
                    <th key={f}>{f}</th>
                  ))}
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={String(row.id || row._id || i)}>
                    {displayFields.map((f) => (
                      <td key={f} className="mono cell-clip">
                        {formatCell(row[f])}
                      </td>
                    ))}
                    <td>
                      <button
                        type="button"
                        className="btn-sm btn-ghost"
                        onClick={async () => {
                          await navigator.clipboard.writeText(JSON.stringify(row, null, 2))
                          setCopied(String(row.id || i))
                          setTimeout(() => setCopied(''), 1500)
                        }}
                      >
                        {copied === String(row.id || i) ? '已复制' : 'JSON'}
                      </button>
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td colSpan={displayFields.length + 1} className="muted">
                      {selected ? '无匹配结果 / 暂无文档' : '请选择集合'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="row">
            <button className="btn-sm" disabled={busy || page <= 1} onClick={() => void query(page - 1)}>
              上一页
            </button>
            <button className="btn-sm" disabled={busy || page * 50 >= total} onClick={() => void query(page + 1)}>
              下一页
            </button>
          </div>
        </section>
      </div>
    </div>
  )
}

function formatCell(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
