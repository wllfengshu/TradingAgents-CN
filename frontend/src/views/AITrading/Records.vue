<template>
  <div class="ai-trading-records">
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Document /></el-icon>
        AI交易-交易记录
      </h1>
      <p class="page-description">
        查看 AI 交易任务的历史执行结果、交易信号和下单状态。
      </p>
    </div>

    <el-card class="filter-card" shadow="never">
      <el-row :gutter="16" align="middle">
        <el-col :xs="24" :sm="12" :md="6">
          <el-select
            v-model="modeFilter"
            placeholder="交易模式"
            clearable
            @change="handleFilterChange"
          >
            <el-option label="模拟交易" value="paper" />
            <el-option label="实盘交易" value="live" />
          </el-select>
        </el-col>

        <el-col :xs="24" :sm="12" :md="6">
          <el-select
            v-model="statusFilter"
            placeholder="任务状态"
            clearable
            @change="handleFilterChange"
          >
            <el-option label="已完成" value="completed" />
            <el-option label="运行中" value="running" />
            <el-option label="等待中" value="pending" />
            <el-option label="失败" value="failed" />
          </el-select>
        </el-col>

        <el-col :xs="24" :sm="24" :md="8">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
            @change="handleFilterChange"
          />
        </el-col>

        <el-col :xs="24" :sm="24" :md="4">
          <div class="action-buttons">
            <el-button @click="resetFilters">重置</el-button>
            <el-button type="primary" @click="refreshRecords">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card class="records-list-card" shadow="never">
      <el-table
        :data="records"
        v-loading="loading"
        style="width: 100%"
        empty-text="暂无交易记录"
      >
        <el-table-column prop="created_at" label="执行时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column label="执行方式" width="110">
          <template #default="{ row }">
            <el-tag
              :type="row.trigger_type === 'scheduled' ? 'warning' : 'info'"
              size="small"
              effect="plain"
            >
              {{ row.trigger_type === 'scheduled' ? '定时执行' : '手动执行' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="模式" width="100">
          <template #default="{ row }">
            <el-tag :type="getModeTagType(row.mode)" effect="dark">
              {{ getModeText(row.mode) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="交易决策" min-width="160">
          <template #default="{ row }">
            <template v-if="row.result?.decision?.action">
              <el-tag :type="getActionTagType(row.result.decision.action)">
                {{ row.result.decision.action }}
              </el-tag>
            </template>
            <template v-else-if="row.status === 'failed'">
              <span class="muted-text">执行失败</span>
            </template>
            <template v-else>
              <span class="muted-text">-</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="涉及标的" min-width="240">
          <template #default="{ row }">
            <template v-if="getSignalStocks(row).length">
              <div class="stock-tags">
                <el-tag
                  v-for="stock in getSignalStocks(row).slice(0, 4)"
                  :key="stock.code"
                  size="small"
                  effect="plain"
                  class="stock-tag"
                >
                  {{ stock.name || stock.code }}
                </el-tag>
                <el-tag
                  v-if="getSignalStocks(row).length > 4"
                  size="small"
                  type="info"
                >
                  +{{ getSignalStocks(row).length - 4 }}
                </el-tag>
              </div>
            </template>
            <template v-else>
              <span class="muted-text">-</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="任务状态" width="140">
          <template #default="{ row }">
            <div class="status-cell">
              <el-tag :type="getStatusType(row.status)">
                {{ getStatusText(row.status) }}
              </el-tag>
              <span
                v-if="row.status !== 'completed' && row.status !== 'failed'"
                class="status-progress"
              >
                {{ row.progress || 0 }}%
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="current_step" label="当前阶段" min-width="160" show-overflow-tooltip />

        <el-table-column label="耗时" width="120">
          <template #default="{ row }">
            {{ formatDuration(row.elapsed_time ?? row.result?.elapsed_time) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" text size="small" @click="viewDetail(row)">
              查看详情
            </el-button>
            <el-button type="danger" text size="small" @click="deleteRecord(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="totalRecords"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Refresh } from '@element-plus/icons-vue'
import {
  aiTradingApi,
  type AiTradingHistoryItem,
  type AiTradingMode,
  type AiTradingRecordQuery,
  type AiTradingStatus,
} from '@/api/aiTrading'
import { formatDateTime } from '@/utils/datetime'

const router = useRouter()

const loading = ref(false)
const modeFilter = ref<AiTradingMode | ''>('')
const statusFilter = ref<AiTradingStatus | ''>('')
const dateRange = ref<string[] | null>(null)
const currentPage = ref(1)
const pageSize = ref(20)
const totalRecords = ref(0)
const records = ref<AiTradingHistoryItem[]>([])

const buildQuery = (): AiTradingRecordQuery => {
  const [startDate, endDate] = dateRange.value || []

  return {
    mode: modeFilter.value || undefined,
    status: statusFilter.value || undefined,
    start_date: startDate || undefined,
    end_date: endDate || undefined,
    page: currentPage.value,
    page_size: pageSize.value,
  }
}

const fetchRecords = async () => {
  loading.value = true

  try {
    const res = await aiTradingApi.getRecords(buildQuery())
    if (res.success) {
      records.value = res.data.tasks || []
      totalRecords.value = res.data.total || 0
    }
  } catch (error) {
    console.error('获取 AI 交易记录失败:', error)
    ElMessage.error('获取 AI 交易记录失败')
  } finally {
    loading.value = false
  }
}

const handleFilterChange = () => {
  currentPage.value = 1
  fetchRecords()
}

const resetFilters = () => {
  modeFilter.value = ''
  statusFilter.value = ''
  dateRange.value = null
  currentPage.value = 1
  fetchRecords()
}

const refreshRecords = () => fetchRecords()

const viewDetail = (row: AiTradingHistoryItem) => {
  router.push(`/ai-trading/records/${row.task_id}`)
}

const deleteRecord = async (row: AiTradingHistoryItem) => {
  try {
    await ElMessageBox.confirm('确定要删除这条 AI 交易记录吗？', '确认删除', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })

    const res = await aiTradingApi.deleteRecord(row.task_id)
    if (res.success) {
      ElMessage.success('记录已删除')
      if (records.value.length === 1 && currentPage.value > 1) {
        currentPage.value -= 1
      }
      fetchRecords()
    }
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('删除 AI 交易记录失败:', error)
    }
  }
}

const getSignalStocks = (row: AiTradingHistoryItem) => {
  const signals = row.result?.trading_signals || []
  const uniqueStocks = new Map<string, { code: string; name: string }>()

  signals.forEach((signal) => {
    if (signal.code && !uniqueStocks.has(signal.code)) {
      uniqueStocks.set(signal.code, {
        code: signal.code,
        name: signal.name,
      })
    }
  })

  return Array.from(uniqueStocks.values())
}

const getActionTagType = (action?: string) => {
  if (!action) return 'info'
  if (action.includes('买入') || action.includes('建仓')) return 'success'
  if (action.includes('卖出')) return 'danger'
  if (action.includes('减仓') || action.includes('调仓')) return 'warning'
  return 'info'
}

const getStatusType = (status: AiTradingStatus) => {
  const map: Record<AiTradingStatus, 'success' | 'warning' | 'danger' | 'info'> = {
    completed: 'success',
    running: 'warning',
    pending: 'info',
    failed: 'danger',
  }

  return map[status] || 'info'
}

const getStatusText = (status: AiTradingStatus) => {
  const map: Record<AiTradingStatus, string> = {
    completed: '已完成',
    running: '运行中',
    pending: '等待中',
    failed: '失败',
  }

  return map[status] || status
}

const getModeTagType = (mode: AiTradingMode) => (mode === 'live' ? 'danger' : 'success')

const getModeText = (mode: AiTradingMode) => (mode === 'live' ? '实盘' : '模拟')

const formatDuration = (seconds?: number) => {
  if (seconds === undefined || seconds === null) return '-'
  if (seconds < 60) return `${seconds.toFixed(1)}s`

  const minutes = Math.floor(seconds / 60)
  const remain = Math.round(seconds % 60)
  return remain > 0 ? `${minutes}分${remain}秒` : `${minutes}分`
}

const formatTime = (time?: string) => (time ? formatDateTime(time) : '-')

const handleSizeChange = (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  fetchRecords()
}

const handleCurrentChange = (page: number) => {
  currentPage.value = page
  fetchRecords()
}

onMounted(() => fetchRecords())
</script>

<style lang="scss" scoped>
.ai-trading-records {
  .page-header {
    margin-bottom: 24px;

    .page-title {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 8px;
      font-size: 24px;
      font-weight: 600;
      color: var(--el-text-color-primary);
    }

    .page-description {
      margin: 0;
      color: var(--el-text-color-regular);
    }
  }

  .filter-card {
    margin-bottom: 24px;

    :deep(.el-select),
    :deep(.el-date-editor) {
      width: 100%;
    }

    .action-buttons {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      width: 100%;
    }
  }

  .records-list-card {
    .stock-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    .status-cell {
      display: flex;
      flex-direction: column;
      gap: 6px;
      align-items: flex-start;
    }

    .status-progress,
    .muted-text {
      color: var(--el-text-color-placeholder);
      font-size: 12px;
    }

    .pagination-wrapper {
      display: flex;
      justify-content: center;
      margin-top: 24px;
    }
  }
}
</style>
