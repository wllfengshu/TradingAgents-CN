import { request, type ApiResponse } from './request'

export type AiTradingMode = 'paper' | 'live'
export type AiTradingStatus = 'pending' | 'running' | 'completed' | 'failed'
export type AnalystTagType = 'success' | 'warning' | 'danger' | 'info'

export interface AiTradingRunRequest {
  mode: AiTradingMode
}

export interface AiTradingTaskStatus {
  task_id: string
  user_id?: string
  mode?: AiTradingMode
  status: AiTradingStatus
  progress: number
  current_step: string
  error_message?: string
  created_at?: string
  updated_at?: string
}

export interface AiTradingAccountInfo {
  cash: number
  total_value: number
  frozen_cash: number
}

export interface AiTradingPosition {
  code: string
  name: string
  volume: number
  cost_price: number
  current_price: number
}

export interface AiTradingAnalystResult {
  name: string
  conclusion: string
  tag_type: AnalystTagType | string
  content: string
}

export interface AiTradingSignal {
  code: string
  name: string
  action: string
  price?: number
  volume?: number
  amount?: number
  reason?: string
}

export interface AiTradingOrderResult {
  code: string
  name: string
  action: string
  price?: number
  volume?: number
  amount?: number
  order_id?: string | null
  simulated_cost?: number
  success: boolean
  error?: string | null
}

export interface AiTradingDecision {
  action: string
  reasoning: string
  position_suggestion?: string
  risk_warning?: string
}

export interface AiTradingExecutionTrace {
  executed_nodes: string[]
  node_counts: Record<string, number>
  mandatory_stage_status: Record<string, boolean>
}

export interface AiTradingResult {
  task_id: string
  user_id?: string
  mode?: AiTradingMode
  status: string
  progress: number
  current_step: string
  elapsed_time?: number
  early_stop?: boolean
  early_stop_reason?: string
  error_message?: string
  account_info?: AiTradingAccountInfo
  positions?: AiTradingPosition[]
  analyst_results?: AiTradingAnalystResult[]
  trading_signals?: AiTradingSignal[]
  order_results?: AiTradingOrderResult[]
  decision?: AiTradingDecision
  decision_report?: string
  execution_trace?: AiTradingExecutionTrace
  completed_at?: string
  created_at?: string
  updated_at?: string
}

export interface AiTradingHistoryItem {
  task_id: string
  user_id: string
  mode: AiTradingMode
  trigger_type?: 'manual' | 'scheduled'
  status: AiTradingStatus
  progress: number
  current_step: string
  created_at: string
  updated_at: string
  elapsed_time?: number
  error_message?: string
  result?: Partial<AiTradingResult>
}

export interface AiTradingHistoryList {
  tasks: AiTradingHistoryItem[]
  total: number
  page: number
  page_size: number
}

export interface AiTradingRecordQuery {
  mode?: AiTradingMode
  status?: AiTradingStatus
  start_date?: string
  end_date?: string
  page?: number
  page_size?: number
}

export interface AiTradingHolding {
  code: string
  name: string
  volume: number
  cost_price: number
  current_price: number
  market_value: number
  pnl: number
  pnl_pct: number
}

export interface AiTradingDailyReturn {
  date: string
  net_amount: number
  trade_count: number
}

export interface AiTradingRecentOrder {
  code: string
  name: string
  action: string
  price: number
  volume: number
  amount: number
  simulated_cost: number
  created_at: string
}

export interface AiTradingPortfolio {
  mode: AiTradingMode
  cash: number
  total_value: number
  initial_capital: number
  total_return: number
  total_return_pct: number
  holdings: AiTradingHolding[]
  daily_returns: AiTradingDailyReturn[]
  sharpe_ratio: number
  max_drawdown: number
  max_drawdown_pct: number
  win_rate: number
  recent_orders: AiTradingRecentOrder[]
  has_data: boolean
}

export interface AiTradingNavPoint {
  date: string
  nav: number
  return_pct: number
}

export interface AiTradingTradeCalendar {
  date: string
  action: string
  code: string
  name: string
  amount: number
}

export interface AiTradingPortfolioHistory {
  mode: AiTradingMode
  nav_curve: AiTradingNavPoint[]
  trade_calendar: AiTradingTradeCalendar[]
  has_data: boolean
}

export interface AiTradingSchedule {
  cron_expression: string
  enabled: boolean
  job_id: string
  next_run_time: string | null
}

export interface CronPreview {
  cron_expression: string
  description: string
  next_run_times: string[]
}

export const aiTradingApi = {
  run(data: AiTradingRunRequest) {
    return request.post<any, ApiResponse<{ task_id: string; status: string; message: string }>>(
      '/api/ai-trading/run',
      data
    )
  },

  getStatus(taskId: string) {
    return request.get<any, ApiResponse<AiTradingTaskStatus>>(
      `/api/ai-trading/status/${taskId}`
    )
  },

  getResult(taskId: string) {
    return request.get<any, ApiResponse<AiTradingResult>>(
      `/api/ai-trading/result/${taskId}`
    )
  },

  stop(taskId: string) {
    return request.post<any, ApiResponse<{ message: string }>>(
      `/api/ai-trading/stop/${taskId}`
    )
  },

  getRecords(params: AiTradingRecordQuery = {}, skipErrorHandler = true) {
    return request.get<any, ApiResponse<AiTradingHistoryList>>(
      '/api/ai-trading/records',
      { params, skipErrorHandler } as any
    )
  },

  getRecordDetail(taskId: string, skipErrorHandler = true) {
    return request.get<any, ApiResponse<AiTradingResult>>(
      `/api/ai-trading/records/${taskId}`,
      { skipErrorHandler } as any
    )
  },

  deleteRecord(taskId: string) {
    return request.delete<any, ApiResponse<{ message: string }>>(
      `/api/ai-trading/records/${taskId}`
    )
  },

  getPortfolio(mode: AiTradingMode = 'paper') {
    return request.get<any, ApiResponse<AiTradingPortfolio>>(
      '/api/ai-trading/portfolio',
      { params: { mode } }
    )
  },

  getPortfolioHistory(mode: AiTradingMode = 'paper', days: number = 30) {
    return request.get<any, ApiResponse<AiTradingPortfolioHistory>>(
      '/api/ai-trading/portfolio/history',
      { params: { mode, days } }
    )
  },

  initPaperPortfolio(initialCapital: number = 1000000) {
    return request.post<any, ApiResponse<{ user_id: string; mode: string; initial_capital: number; message: string }>>(
      '/api/ai-trading/portfolio/init',
      { initial_capital: initialCapital }
    )
  },

  /** 创建AI交易定时任务 */
  createSchedule(cronExpression: string) {
    return request.post<any, ApiResponse<AiTradingSchedule>>(
      '/api/ai-trading/schedule',
      { cron_expression: cronExpression }
    )
  },

  /** 获取AI交易定时任务 */
  getSchedule() {
    return request.get<any, ApiResponse<AiTradingSchedule | null>>(
      '/api/ai-trading/schedule'
    )
  },

  /** 删除AI交易定时任务 */
  deleteSchedule() {
    return request.delete<any, ApiResponse<{ message: string }>>(
      '/api/ai-trading/schedule'
    )
  },

  /** 预览Cron表达式 */
  previewCron(cronExpression: string, count: number = 5) {
    return request.post<any, ApiResponse<CronPreview>>(
      '/api/ai-trading/schedule/preview',
      { cron_expression: cronExpression, count }
    )
  },
}
