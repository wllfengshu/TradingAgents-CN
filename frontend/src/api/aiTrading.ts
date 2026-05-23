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
  completed_at?: string
  created_at?: string
  updated_at?: string
}

export interface AiTradingHistoryItem {
  task_id: string
  user_id: string
  mode: AiTradingMode
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
}
