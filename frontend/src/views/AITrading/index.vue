<template>
  <div class="ai-trading">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="title-section">
          <h1 class="page-title">
            <el-icon class="title-icon"><TrendCharts /></el-icon>
            AI交易
          </h1>
          <p class="page-description">
            AI驱动的智能交易系统，支持实盘与模拟两种模式
          </p>
        </div>
      </div>
    </div>

    <!-- 主要内容区 -->
    <div class="trading-container">
      <!-- 模式选择 & 运行控制 -->
      <el-card class="control-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <h3>交易控制</h3>
          </div>
        </template>

        <!-- 模式选择 -->
        <div class="mode-section">
          <span class="mode-label">交易模式：</span>
          <el-radio-group v-model="tradingMode" size="large" class="mode-group">
            <el-radio-button label="live">
              <el-icon><Promotion /></el-icon>
              实盘模式
            </el-radio-button>
            <el-radio-button label="paper">
              <el-icon><Monitor /></el-icon>
              模拟模式
            </el-radio-button>
          </el-radio-group>
          <el-tag
            :type="tradingMode === 'live' ? 'danger' : 'success'"
            effect="dark"
            class="mode-tag"
          >
            {{ tradingMode === 'live' ? '实盘' : '模拟' }}
          </el-tag>
        </div>

        <!-- 实盘风险提示 -->
        <el-alert
          v-if="tradingMode === 'live'"
          type="error"
          :closable="false"
          show-icon
          class="risk-alert"
        >
          <template #title>
            <span style="font-weight: bold;">实盘模式涉及真实资金交易，请谨慎操作！</span>
          </template>
        </el-alert>

        <!-- 模拟模式提示 -->
        <el-alert
          v-if="tradingMode === 'paper'"
          type="info"
          :closable="false"
          show-icon
          class="risk-alert"
        >
          <template #title>
            <span>模拟模式使用虚拟资金，仅供学习和策略验证</span>
          </template>
        </el-alert>

        <!-- 运行按钮 -->
        <div class="action-buttons">
          <el-button
            type="primary"
            size="large"
            :loading="running"
            :disabled="running"
            class="run-btn"
            @click="handleRun"
          >
            <el-icon><VideoPlay /></el-icon>
            {{ running ? '运行中...' : '运行' }}
          </el-button>

          <el-button
            size="large"
            class="stop-btn"
            :disabled="!running"
            @click="handleStop"
          >
            <el-icon><VideoPause /></el-icon>
            停止
          </el-button>
        </div>

        <!-- 运行进度 -->
        <div v-if="running" class="progress-section">
          <el-progress
            :percentage="progress"
            :stroke-width="12"
            :status="progressStatus"
          />
          <div class="progress-info">
            <span class="progress-step">{{ currentStep }}</span>
            <span class="progress-time">已用时间：{{ formatTime(elapsedTime) }}</span>
          </div>
        </div>
      </el-card>

      <!-- 操作记录 -->
      <el-card class="records-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <h3>操作记录</h3>
            <div class="card-actions">
              <el-button text size="small" @click="fetchRecords">
                <el-icon><Refresh /></el-icon>
                刷新
              </el-button>
            </div>
          </div>
        </template>

        <el-table
          :data="records"
          v-loading="loadingRecords"
          size="small"
          class="records-table"
          :default-sort="{ prop: 'created_at', order: 'descending' }"
        >
          <el-table-column label="时间" width="170" prop="created_at" sortable>
            <template #default="{ row }">
              {{ formatDateTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column label="模式" width="100">
            <template #default="{ row }">
              <el-tag
                :type="row.mode === 'live' ? 'danger' : 'success'"
                size="small"
                effect="plain"
              >
                {{ row.mode === 'live' ? '实盘' : '模拟' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-tag
                :type="getActionTagType(row.action)"
                size="small"
              >
                {{ row.action }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="标的" min-width="120">
            <template #default="{ row }">
              <template v-if="row.stocks && row.stocks.length > 0">
                <el-tag
                  v-for="stock in row.stocks"
                  :key="stock.code"
                  size="small"
                  effect="plain"
                  class="stock-tag"
                >
                  {{ stock.code }} {{ stock.name }}
                </el-tag>
              </template>
              <span v-else style="color: #909399;">-</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag
                :type="getStatusTagType(row.status)"
                size="small"
              >
                {{ getStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span>{{ row.detail || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="100">
            <template #default="{ row }">
              <span v-if="row.elapsed_time">{{ formatTime(row.elapsed_time) }}</span>
              <span v-else style="color: #909399;">-</span>
            </template>
          </el-table-column>
        </el-table>

        <!-- 空状态 -->
        <div v-if="!loadingRecords && records.length === 0" class="empty-section">
          <el-empty description="暂无操作记录">
            <template #image>
              <div class="empty-icon">
                <el-icon :size="64" color="#c0c4cc"><TrendCharts /></el-icon>
              </div>
            </template>
          </el-empty>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  TrendCharts,
  VideoPlay,
  VideoPause,
  Promotion,
  Monitor,
  Refresh,
} from '@element-plus/icons-vue'
import { aiTradingApi, type AiTradingRecord } from '@/api/aiTrading'
import { formatDateTime } from '@/utils/datetime'

// 交易模式
const tradingMode = ref<'paper' | 'live'>('paper')

// 运行状态
const running = ref(false)
const progress = ref(0)
const currentStep = ref('')
const elapsedTime = ref(0)
const currentTaskId = ref('')
const progressStatus = ref<'' | 'success' | 'exception' | 'warning'>('')
let timer: ReturnType<typeof setInterval> | null = null
let pollingTimer: ReturnType<typeof setInterval> | null = null

// 操作记录
const records = ref<AiTradingRecord[]>([])
const loadingRecords = ref(false)

// 格式化时间
const formatTime = (seconds: number): string => {
  if (!seconds || seconds <= 0) return '0秒'
  if (seconds < 60) return `${Math.floor(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  return remainingSeconds > 0 ? `${minutes}分${remainingSeconds}秒` : `${minutes}分钟`
}

// 获取操作标签类型
const getActionTagType = (action: string): 'success' | 'warning' | 'danger' | 'info' => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    '买入': 'success',
    '卖出': 'danger',
    '分析': 'primary' as any,
    '决策': 'warning',
  }
  return map[action] || 'info'
}

// 获取状态标签类型
const getStatusTagType = (status: string): 'success' | 'warning' | 'danger' | 'info' => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    'completed': 'success',
    'running': 'warning',
    'failed': 'danger',
    'pending': 'info',
  }
  return map[status] || 'info'
}

// 获取状态标签文案
const getStatusLabel = (status: string): string => {
  const map: Record<string, string> = {
    'completed': '已完成',
    'running': '运行中',
    'failed': '失败',
    'pending': '等待中',
  }
  return map[status] || status
}

// 运行AI交易
const handleRun = async () => {
  if (tradingMode.value === 'live') {
    try {
      await ElMessageBox.confirm(
        '实盘模式将使用真实资金进行交易，确认继续？',
        '实盘交易确认',
        {
          confirmButtonText: '确认运行',
          cancelButtonText: '取消',
          type: 'warning',
        }
      )
    } catch {
      return
    }
  }

  running.value = true
  progress.value = 0
  elapsedTime.value = 0
  currentStep.value = '正在提交AI交易任务...'
  progressStatus.value = ''

  timer = setInterval(() => {
    elapsedTime.value += 1
  }, 1000)

  try {
    const res = await aiTradingApi.run({ mode: tradingMode.value })
    if (!res.success) {
      throw new Error(res.message || '启动任务失败')
    }

    currentTaskId.value = res.data.task_id
    currentStep.value = '任务已提交，等待处理...'
    progress.value = 5

    startPolling()
  } catch (error: any) {
    running.value = false
    if (timer) { clearInterval(timer); timer = null }
    ElMessage.error(error.message || '启动AI交易任务失败')
  }
}

// 停止运行
const handleStop = async () => {
  try {
    await ElMessageBox.confirm('确认停止当前AI交易任务？', '停止确认', {
      confirmButtonText: '确认停止',
      cancelButtonText: '取消',
      type: 'warning',
    })

    if (currentTaskId.value) {
      await aiTradingApi.stop(currentTaskId.value)
    }

    if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
    if (timer) { clearInterval(timer); timer = null }

    running.value = false
    progressStatus.value = 'exception'
    currentStep.value = '已手动停止'
    ElMessage.info('AI交易任务已停止')

    await fetchRecords()
  } catch {
    // 用户取消
  }
}

// 轮询任务状态
const startPolling = () => {
  if (pollingTimer) clearInterval(pollingTimer)

  pollingTimer = setInterval(async () => {
    if (!currentTaskId.value) return

    try {
      const res = await aiTradingApi.getStatus(currentTaskId.value)
      if (!res.success || !res.data) return

      const task = res.data
      progress.value = task.progress || 0
      currentStep.value = task.current_step || ''

      if (task.status === 'completed') {
        if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
        if (timer) { clearInterval(timer); timer = null }

        progress.value = 100
        progressStatus.value = 'success'
        currentStep.value = '交易完成'
        running.value = false

        ElMessage.success('AI交易任务完成')
        await fetchRecords()
      } else if (task.status === 'failed') {
        if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
        if (timer) { clearInterval(timer); timer = null }

        progressStatus.value = 'exception'
        currentStep.value = '交易失败'
        running.value = false

        const errMsg = task.error_message || '交易过程中发生错误'
        ElMessage.error(errMsg)
        await fetchRecords()
      }
    } catch (error) {
      console.error('轮询AI交易任务状态失败:', error)
    }
  }, 3000)
}

// 获取操作记录
const fetchRecords = async () => {
  try {
    loadingRecords.value = true
    const res = await aiTradingApi.getRecords({ mode: tradingMode.value })
    if (res.success && res.data) {
      records.value = res.data.items || []
    }
  } catch (error: any) {
    // 后端接口尚未就绪时静默处理，不弹出错误提示
    console.warn('获取操作记录失败（后端接口可能尚未实现）:', error?.message || error)
  } finally {
    loadingRecords.value = false
  }
}

onMounted(() => {
  fetchRecords()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (pollingTimer) clearInterval(pollingTimer)
})
</script>

<style lang="scss" scoped>
.ai-trading {
  min-height: 100vh;
  background: var(--el-bg-color-page);
  padding: 24px;

  .page-header {
    margin-bottom: 32px;

    .header-content {
      background: var(--el-bg-color);
      padding: 32px;
      border-radius: 16px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
    }

    .title-section {
      .page-title {
        display: flex;
        align-items: center;
        font-size: 32px;
        font-weight: 700;
        color: #1a202c;
        margin: 0 0 8px 0;

        .title-icon {
          margin-right: 12px;
          color: #3b82f6;
        }
      }

      .page-description {
        font-size: 16px;
        color: #64748b;
        margin: 0;
      }
    }
  }

  .trading-container {
    .control-card,
    .records-card {
      border-radius: 16px;
      border: none;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
      margin-bottom: 24px;

      :deep(.el-card__header) {
        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        color: white;
        border-radius: 16px 16px 0 0;
        padding: 20px 24px;

        .card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;

          h3 {
            margin: 0;
            font-size: 18px;
            font-weight: 600;
          }

          .card-actions {
            color: white;
          }
        }
      }

      :deep(.el-card__body) {
        padding: 24px;
      }
    }

    // 模式选择
    .mode-section {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 20px;

      .mode-label {
        font-size: 15px;
        font-weight: 600;
        color: #1a202c;
        white-space: nowrap;
      }

      .mode-group {
        :deep(.el-radio-button__inner) {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 12px 24px;
          font-size: 15px;
          font-weight: 600;
        }
      }

      .mode-tag {
        font-size: 13px;
      }
    }

    .risk-alert {
      margin-bottom: 20px;
    }

    // 运行按钮
    .action-buttons {
      display: flex;
      justify-content: center;
      gap: 20px;

      .run-btn {
        width: 200px;
        height: 56px;
        font-size: 18px;
        font-weight: 700;
        border-radius: 16px;
        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        border: none;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(59, 130, 246, 0.2);

        &:hover {
          transform: translateY(-3px);
          box-shadow: 0 12px 30px rgba(59, 130, 246, 0.4);
        }

        &:disabled {
          opacity: 0.6;
          transform: none;
          box-shadow: 0 4px 15px rgba(59, 130, 246, 0.1);
        }

        .el-icon {
          margin-right: 8px;
          font-size: 20px;
        }
      }

      .stop-btn {
        width: 160px;
        height: 56px;
        font-size: 18px;
        font-weight: 700;
        border-radius: 16px;
        border: 2px solid #f56c6c;
        color: #f56c6c;
        transition: all 0.3s ease;

        &:hover:not(:disabled) {
          background: #fef0f0;
          transform: translateY(-3px);
          box-shadow: 0 8px 25px rgba(245, 108, 108, 0.15);
        }

        &:disabled {
          opacity: 0.4;
        }

        .el-icon {
          margin-right: 8px;
          font-size: 20px;
        }
      }
    }

    // 进度
    .progress-section {
      margin-top: 24px;
      padding: 20px;
      background: #f8fafc;
      border-radius: 12px;

      .progress-info {
        display: flex;
        justify-content: space-between;
        margin-top: 12px;
        font-size: 13px;
        color: #64748b;

        .progress-step {
          font-weight: 500;
        }
      }
    }

    // 操作记录
    .records-table {
      width: 100%;

      .stock-tag {
        margin-right: 4px;
        margin-bottom: 2px;
      }
    }

    // 空状态
    .empty-section {
      padding: 40px 0;

      .empty-icon {
        margin-bottom: 16px;
      }
    }
  }
}
</style>
