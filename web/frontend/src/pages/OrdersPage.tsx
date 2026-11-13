import { useEffect, useState } from 'react'
import { api, type OrderRow } from '../api'

export default function OrdersPage() {
  const [items, setItems] = useState<OrderRow[]>([])
  const [code, setCode] = useState('')
  const [status, setStatus] = useState('')
  const [env, setEnv] = useState('')
  const [error, setError] = useState('')

  async function load() {
    try {
      setError('')
      const res = await api.listOrders({
        code: code || undefined,
        status: status || undefined,
        env: env || undefined,
      })
      setItems(res.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    void load()
  }, [])

  return (
    <div className="orders-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">订单</h1>
          <p className="page-lead">只读查看委托与成交。</p>
        </div>
        <button className="primary btn-sm" type="button" onClick={() => void load()}>
          查询
        </button>
      </div>
      {error && <p className="danger">{error}</p>}

      <div className="card compact-card">
        <div className="row filter-row" style={{ marginBottom: '0.75rem' }}>
          <input className="ctl-sm" placeholder="代码" value={code} onChange={(e) => setCode(e.target.value)} />
          <input className="ctl-sm" placeholder="状态" value={status} onChange={(e) => setStatus(e.target.value)} />
          <input className="ctl-sm" placeholder="环境" value={env} onChange={(e) => setEnv(e.target.value)} />
        </div>

        <div className="table-scroll">
          <table className="table-dense">
            <thead>
              <tr>
                <th>order_id</th>
                <th>代码</th>
                <th>方向</th>
                <th>数量</th>
                <th>状态</th>
                <th>环境</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.id}>
                  <td className="mono cell-clip">{String(o.order_id ?? o.id)}</td>
                  <td>{String(o.code ?? '')}</td>
                  <td>{String(o.side ?? o.direction ?? '')}</td>
                  <td className="mono">{String(o.qty ?? o.volume ?? o.quantity ?? '')}</td>
                  <td>{String(o.status ?? '')}</td>
                  <td>{String(o.env ?? o.account_type ?? o.mode ?? '')}</td>
                  <td className="mono muted">{String(o.created_at ?? o.trade_date ?? o.updated_at ?? '')}</td>
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td colSpan={7} className="muted">
                    暂无成交/委托
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
