const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => request<{ ok: boolean }>('/api/health'),
  workbenchSummary: () => request<WorkbenchSummary>('/api/workbench/summary'),
  factorCatalog: () =>
    request<{ items: FactorMeta[]; by_layer: Record<string, FactorMeta[]> }>('/api/factors/catalog'),
  defaultTemplate: () => request<StrategyTemplate>('/api/strategies/default-template'),
  listStrategies: () => request<{ items: StrategySummary[] }>('/api/strategies'),
  getStrategy: (id: string) => request<StrategyDetail>(`/api/strategies/${id}`),
  createStrategy: (body: StrategyCreateBody) =>
    request<StrategyDetail>('/api/strategies', { method: 'POST', body: JSON.stringify(body) }),
  updateStrategy: (id: string, body: Partial<StrategyCreateBody> & { params?: unknown }) =>
    request<StrategyDetail>(`/api/strategies/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  runBacktest: (id: string, body?: { start_date?: string; end_date?: string; use_precomputed?: boolean }) =>
    request<{ job_id: string; backtest_id: string; status: string }>(`/api/strategies/${id}/backtest`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),
  getBacktest: (id: string) => request<BacktestDetail>(`/api/backtests/${id}`),
  listBacktests: (strategyId?: string) =>
    request<{ items: BacktestSummary[] }>(
      strategyId ? `/api/backtests?strategy_id=${encodeURIComponent(strategyId)}` : '/api/backtests',
    ),
  dataStats: () => request<{ items: DataStat[] }>('/api/data/stats'),
  triggerData: (action: string, extra?: Record<string, number>) =>
    request<{ job_id: string }>('/api/data/trigger', {
      method: 'POST',
      body: JSON.stringify({ action, ...(extra || {}) }),
    }),
  getJob: (id: string) => request<JobInfo>(`/api/jobs/${id}`),
  listJobs: () => request<{ items: JobInfo[] }>('/api/jobs'),
  nightlyStatus: () => request<NightlyStatus>('/api/jobs/scheduler/nightly'),
  nightlyAction: (action: string) =>
    request<{ ok?: boolean; message?: string }>('/api/jobs/scheduler/nightly', {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  listOrders: (q?: { code?: string; status?: string; env?: string }) => {
    const params = new URLSearchParams()
    if (q?.code) params.set('code', q.code)
    if (q?.status) params.set('status', q.status)
    if (q?.env) params.set('env', q.env)
    const qs = params.toString()
    return request<{ items: OrderRow[]; count: number }>(`/api/orders${qs ? `?${qs}` : ''}`)
  },
  sourcesStatus: () => request<{ items: SourceStatus[] }>('/api/sources/status'),
  sourcesTest: (source: 'akshare' | 'qmt_bridge' | 'stockdb' | 'all') =>
    request<{ items: SourceStatus[] }>('/api/sources/test', {
      method: 'POST',
      body: JSON.stringify({ source }),
    }),
  mongoStatus: () => request<MongoStatus>('/api/mongo/status'),
  mongoCollections: () => request<{ items: MongoCollection[] }>('/api/mongo/collections'),
  mongoQuery: (q: {
    collection: string
    code?: string
    start?: string
    end?: string
    filter?: string
    page?: number
    page_size?: number
  }) => {
    const params = new URLSearchParams()
    params.set('collection', q.collection)
    if (q.code) params.set('code', q.code)
    if (q.start) params.set('start', q.start)
    if (q.end) params.set('end', q.end)
    if (q.filter) params.set('filter', q.filter)
    params.set('page', String(q.page || 1))
    params.set('page_size', String(q.page_size || 50))
    return request<MongoQueryResult>(`/api/mongo/query?${params.toString()}`)
  },
}

export type QuoteItem = {
  name: string
  price: number | null
  change_pct: number | null
  message?: string
}

export type NewsItem = {
  title: string
  summary?: string
  url?: string
  published_at?: string
}

export type WorkbenchSummary = {
  headline: string
  ohlcv_latest?: string | null
  nightly: {
    exists: boolean
    status?: string | null
    next_run?: string | null
    last_run?: string | null
  }
  data_health: DataStat[]
  recent_strategies: StrategySummary[]
  recent_backtests: BacktestSummary[]
  order_count: number
  running_jobs: number
  recent_jobs: {
    id: string
    kind: string
    status: string
    message: string
    created_at: string
  }[]
  market?: { index: QuoteItem; gold: QuoteItem }
  news?: NewsItem[]
}

export type FactorMeta = {
  layer: string
  field: string
  name_cn: string
  default_polarity: string
  regimes: string[]
}

export type FactorEntry = { field: string; weight: number; polarity: string }

export type ActiveFactors = {
  sector?: Record<string, FactorEntry[]>
  dragon?: Record<string, FactorEntry[]>
  force?: Record<string, FactorEntry[]>
  profiles?: unknown
}

export type StrategyTemplate = {
  strategy_name?: string
  description?: string
  version?: string
  active_factors: ActiveFactors
  backtest: Record<string, unknown>
  params: Record<string, unknown>
}

export type StrategyCreateBody = {
  name: string
  description?: string
  active_factors?: ActiveFactors
  backtest?: Record<string, unknown>
}

export type StrategySummary = {
  id: string
  name: string
  description?: string
  strategy_name?: string
  version?: string
  factor_summary?: Record<string, Record<string, number>>
  updated_at?: string
}

export type StrategyDetail = StrategySummary & {
  params: Record<string, unknown> & { active_factors?: ActiveFactors; backtest?: Record<string, unknown> }
}

export type BacktestSummary = {
  id: string
  strategy_id: string
  status: string
  start_date?: string
  end_date?: string
  metrics?: Record<string, number>
  error?: string | null
  created_at?: string
}

export type BacktestDetail = BacktestSummary & {
  equity: { date: string; equity: number }[]
  job_id?: string
}

export type DataStat = { collection: string; count: number; latest_trade_date?: string | null }

export type JobInfo = {
  id: string
  kind: string
  status: string
  message: string
  created_at: string
  finished_at?: string | null
  logs: string[]
  result?: unknown
}

export type NightlyStatus = {
  task_name: string
  exists: boolean
  status?: string | null
  next_run?: string | null
  last_run?: string | null
  logs: string[]
  raw?: string
}

export type OrderRow = Record<string, unknown> & { id: string }

export type SourceStatus = {
  id: string
  name: string
  endpoint?: string
  configured?: boolean
  ok?: boolean | null
  status: string
  latency_ms?: number | null
  message?: string
  checked_at?: string | null
}

export type MongoStatus = {
  ok: boolean
  host: string
  port: number
  database: string
  message: string
}

export type MongoCollection = {
  name: string
  count: number
  latest_trade_date?: string | null
}

export type MongoQueryResult = {
  items: Record<string, unknown>[]
  page: number
  page_size: number
  total_estimate: number
  fields: string[]
}
