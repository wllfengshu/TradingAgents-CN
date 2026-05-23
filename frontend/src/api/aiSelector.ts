/**
 * AI选股API
 */

import { request, type ApiResponse } from './request'

export interface AiSelectorRunRequest {
  quick_model?: string
  deep_model?: string
}

export interface AiSelectorTaskStatus {
  task_id: string
  user_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  current_step: string
  error_message?: string
  created_at: string
  updated_at: string
}

export interface AiSelectorResult {
  task_id: string
  status: string
  progress: number
  current_step: string
  elapsed_time: number
  early_stop?: boolean
  early_stop_reason?: string
  analyst_results: Array<{
    name: string
    conclusion: string
    tag_type: string
    content: string
  }>
  decision: {
    action: string
    stocks: Array<{ code: string; name: string; reason?: string }>
    reasoning: string
    position_suggestion?: string
    risk_warning?: string
  }
  decision_report: string
  completed_at: string
}

export interface AiSelectorHistoryItem {
  task_id: string
  user_id: string
  status: string
  progress: number
  current_step: string
  created_at: string
  updated_at: string
  elapsed_time?: number
  result?: {
    decision?: {
      action: string
      stocks: Array<{ code: string; name: string; reason?: string }>
    }
  }
}

export interface AiSelectorHistoryList {
  tasks: AiSelectorHistoryItem[]
  total: number
  page: number
  page_size: number
}

export interface AiSelectorSchedule {
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

export const aiSelectorApi = {
  /** 启动AI选股任务 */
  run(data: AiSelectorRunRequest = {}) {
    return request.post<any, ApiResponse<{ task_id: string; status: string; message: string }>>(
      '/api/ai-selector/run',
      data
    )
  },

  /** 获取任务状态 */
  getStatus(taskId: string) {
    return request.get<any, ApiResponse<AiSelectorTaskStatus>>(
      `/api/ai-selector/status/${taskId}`
    )
  },

  /** 获取任务结果 */
  getResult(taskId: string) {
    return request.get<any, ApiResponse<AiSelectorResult>>(
      `/api/ai-selector/result/${taskId}`
    )
  },

  /** 获取历史记录列表 */
  getHistory(params: { page?: number; page_size?: number } = {}) {
    return request.get<any, ApiResponse<AiSelectorHistoryList>>(
      '/api/ai-selector/history',
      params
    )
  },

  /** 获取历史记录详情 */
  getHistoryDetail(taskId: string) {
    return request.get<any, ApiResponse<AiSelectorResult>>(
      `/api/ai-selector/history/${taskId}`
    )
  },

  /** 删除历史记录 */
  deleteHistory(taskId: string) {
    return request.delete<any, ApiResponse<{ message: string }>>(
      `/api/ai-selector/history/${taskId}`
    )
  },

  /** 创建AI选股定时任务 */
  createSchedule(cronExpression: string) {
    return request.post<any, ApiResponse<AiSelectorSchedule>>(
      '/api/ai-selector/schedule',
      { cron_expression: cronExpression }
    )
  },

  /** 获取AI选股定时任务 */
  getSchedule() {
    return request.get<any, ApiResponse<AiSelectorSchedule | null>>(
      '/api/ai-selector/schedule'
    )
  },

  /** 删除AI选股定时任务 */
  deleteSchedule() {
    return request.delete<any, ApiResponse<{ message: string }>>(
      '/api/ai-selector/schedule'
    )
  },

  /** 预览Cron表达式 */
  previewCron(cronExpression: string, count: number = 5) {
    return request.post<any, ApiResponse<CronPreview>>(
      '/api/ai-selector/schedule/preview',
      { cron_expression: cronExpression, count }
    )
  },
}
