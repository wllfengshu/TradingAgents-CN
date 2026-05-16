/**
 * AI交易API
 */

import { request, type ApiResponse } from './request'

export interface AiTradingRunRequest {
  mode: 'paper' | 'live'
}

export interface AiTradingTaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  current_step: string
  error_message?: string
}

export interface AiTradingRecord {
  id: string
  mode: 'paper' | 'live'
  action: string
  status: string
  stocks?: Array<{ code: string; name: string }>
  detail?: string
  elapsed_time?: number
  created_at: string
  updated_at?: string
}

export interface AiTradingRecordList {
  items: AiTradingRecord[]
  total: number
  page?: number
  page_size?: number
}

export const aiTradingApi = {
  /** 启动AI交易任务 */
  run(data: AiTradingRunRequest) {
    return request.post<any, ApiResponse<{ task_id: string; status: string; message: string }>>(
      '/api/ai-trading/run',
      data
    )
  },

  /** 获取任务状态 */
  getStatus(taskId: string) {
    return request.get<any, ApiResponse<AiTradingTaskStatus>>(
      `/api/ai-trading/status/${taskId}`
    )
  },

  /** 停止任务 */
  stop(taskId: string) {
    return request.post<any, ApiResponse<{ message: string }>>(
      `/api/ai-trading/stop/${taskId}`
    )
  },

  /** 获取操作记录 */
  getRecords(params: { mode?: string; page?: number; page_size?: number } = {}, skipErrorHandler = true) {
    return request.get<any, ApiResponse<AiTradingRecordList>>(
      '/api/ai-trading/records',
      { params, skipErrorHandler } as any
    )
  },
}
