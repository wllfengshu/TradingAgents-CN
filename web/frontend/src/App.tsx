import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import Layout from './components/Layout'
import WorkbenchPage from './pages/WorkbenchPage'
import FactorsPage from './pages/FactorsPage'
import StrategiesPage from './pages/StrategiesPage'
import StrategyDetailPage from './pages/StrategyDetailPage'
import BacktestPage from './pages/BacktestPage'
import SourcesPage from './pages/SourcesPage'
import DataBrowsePage from './pages/DataBrowsePage'
import JobsPage from './pages/JobsPage'
import OrdersPage from './pages/OrdersPage'

function RedirectStrategiesId() {
  const { id } = useParams()
  return <Navigate to={`/research/strategies/${id}`} replace />
}

function RedirectBacktestsId() {
  const { id } = useParams()
  return <Navigate to={`/research/backtests/${id}`} replace />
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<WorkbenchPage />} />
        <Route path="/research/factors" element={<FactorsPage />} />
        <Route path="/research/studio" element={<Navigate to="/research/factors" replace />} />
        <Route path="/research/strategies" element={<StrategiesPage />} />
        <Route path="/research/strategies/:id" element={<StrategyDetailPage />} />
        <Route path="/research/backtests/:id" element={<BacktestPage />} />
        <Route path="/ops" element={<Navigate to="/ops/sources" replace />} />
        <Route path="/ops/sources" element={<SourcesPage />} />
        <Route path="/ops/data" element={<DataBrowsePage />} />
        <Route path="/ops/jobs" element={<JobsPage />} />
        <Route path="/trade/orders" element={<OrdersPage />} />
        <Route path="/factors" element={<Navigate to="/research/factors" replace />} />
        <Route path="/strategies" element={<Navigate to="/research/strategies" replace />} />
        <Route path="/strategies/:id" element={<RedirectStrategiesId />} />
        <Route path="/backtests/:id" element={<RedirectBacktestsId />} />
        <Route path="/data" element={<Navigate to="/ops/data" replace />} />
        <Route path="/orders" element={<Navigate to="/trade/orders" replace />} />
      </Route>
    </Routes>
  )
}
