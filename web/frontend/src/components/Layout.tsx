import { NavLink, Outlet, useLocation } from 'react-router-dom'
import JobToast from './JobToast'

function crumb(path: string): string {
  if (path === '/') return '工作台'
  if (path.startsWith('/research/factors')) return '研究 / 因子库'
  if (path.startsWith('/research/strategies')) return '研究 / 策略库'
  if (path.startsWith('/research/backtests')) return '研究 / 回测结果'
  if (path.startsWith('/ops/sources')) return '运维 / 数据源'
  if (path.startsWith('/ops/data')) return '运维 / 数据管理'
  if (path.startsWith('/ops/jobs')) return '运维 / 任务与夜跑'
  if (path.startsWith('/trade')) return '交易 / 订单'
  if (path.startsWith('/research')) return '研究'
  if (path.startsWith('/ops')) return '运维'
  return 'ZStock'
}

function navClass(active: boolean) {
  return active ? 'active' : undefined
}

export default function Layout() {
  const path = useLocation().pathname

  return (
    <div className="shell">
      <aside className="sidebar">
        <NavLink to="/" className="brand" end>
          <span className="brand-mark">Z</span>
          ZStock
        </NavLink>

        <nav className="side-nav">
          <NavLink to="/" end className={({ isActive }) => navClass(isActive)}>
            <span className="nav-icon">▣</span>
            工作台
          </NavLink>

          <div className="side-section">
            <div className="side-section-label">研究</div>
            <div className="sub">
              <NavLink
                to="/research/factors"
                className={() =>
                  navClass(path.startsWith('/research/factors') || path.startsWith('/research/studio'))
                }
              >
                因子库
              </NavLink>
              <NavLink
                to="/research/strategies"
                className={() =>
                  navClass(
                    path.startsWith('/research/strategies') || path.startsWith('/research/backtests'),
                  )
                }
              >
                策略库
              </NavLink>
            </div>
          </div>

          <div className="side-section">
            <div className="side-section-label">运维</div>
            <div className="sub">
              <NavLink to="/ops/sources" className={() => navClass(path.includes('/sources'))}>
                数据源
              </NavLink>
              <NavLink to="/ops/data" className={() => navClass(path.includes('/ops/data'))}>
                数据管理
              </NavLink>
              <NavLink to="/ops/jobs" className={() => navClass(path.includes('/jobs'))}>
                任务与夜跑
              </NavLink>
            </div>
          </div>

          <div className="side-section">
            <div className="side-section-label">交易</div>
            <div className="sub">
              <NavLink to="/trade/orders" className={() => navClass(path.startsWith('/trade'))}>
                订单
              </NavLink>
            </div>
          </div>
        </nav>
      </aside>

      <div className="main">
        <div className="main-top">
          <div className="crumb">
            ZStock <span className="crumb-sep">/</span> <strong>{crumb(path)}</strong>
          </div>
        </div>
        <div className="content">
          <Outlet />
        </div>
      </div>
      <JobToast />
    </div>
  )
}
