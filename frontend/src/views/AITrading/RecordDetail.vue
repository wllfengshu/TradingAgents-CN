<template>
  <div class="ai-trading-record-detail">
    <!-- 顶部导航 -->
    <div class="detail-header">
      <el-button @click="goBack" text>
        <el-icon><ArrowLeft /></el-icon>
        返回交易记录
      </el-button>
      <div class="header-info" v-if="record">
        <el-tag :type="getStatusType(record.status)" size="large">
          {{ getStatusText(record.status) }}
        </el-tag>
        <el-tag
          v-if="record.decision?.action"
          :type="getActionTagType(record.decision.action)"
          size="large"
        >
          {{ record.decision.action }}
        </el-tag>
        <span class="header-mode" v-if="record.mode">
          <el-tag :type="record.mode === 'live' ? 'danger' : 'success'" effect="dark" size="small">
            {{ record.mode === 'live' ? '实盘' : '模拟' }}
          </el-tag>
        </span>
        <span class="header-time" v-if="record.completed_at">
          完成时间：{{ formatTime(record.completed_at) }}
        </span>
        <span class="header-elapsed" v-if="record.elapsed_time">
          耗时：{{ formatDuration(record.elapsed_time) }}
        </span>
      </div>
    </div>

    <div v-loading="loading" class="detail-content">
      <template v-if="record">
        <!-- 错误提示 -->
        <el-alert
          v-if="record.status === 'failed'"
          type="error"
          :closable="false"
          show-icon
          style="margin-bottom: 16px;"
        >
          <template #title>
            <span style="font-weight: bold;">任务失败</span>
          </template>
          <template #default>
            {{ record.error_message || '未知错误' }}
          </template>
        </el-alert>

        <!-- 账户信息 -->
        <el-card v-if="record.account_info" class="account-card" shadow="never">
          <template #header>
            <span class="card-title">账户信息</span>
          </template>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="可用资金">
              {{ formatMoney(record.account_info.cash) }}
            </el-descriptions-item>
            <el-descriptions-item label="总资产">
              {{ formatMoney(record.account_info.total_value) }}
            </el-descriptions-item>
            <el-descriptions-item label="冻结资金">
              {{ formatMoney(record.account_info.frozen_cash) }}
            </el-descriptions-item>
          </el-descriptions>

          <!-- 持仓列表 -->
          <div v-if="record.positions?.length" class="positions-section">
            <h4>当前持仓</h4>
            <el-table :data="record.positions" size="small" border>
              <el-table-column prop="code" label="代码" width="120" />
              <el-table-column prop="name" label="名称" width="100" />
              <el-table-column prop="volume" label="持仓(股)" width="100" />
              <el-table-column label="成本价" width="100">
                <template #default="{ row }">{{ row.cost_price?.toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="现价" width="100">
                <template #default="{ row }">{{ row.current_price?.toFixed(2) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>

        <!-- 交易决策 -->
        <el-card v-if="record.decision" class="decision-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span class="card-title">交易决策</span>
              <el-tag :type="getActionTagType(record.decision.action)">
                {{ record.decision.action }}
              </el-tag>
            </div>
          </template>

          <div class="reasoning-section" v-if="record.decision.reasoning">
            <h4>决策依据</h4>
            <p class="reasoning-text">{{ record.decision.reasoning }}</p>
          </div>

          <div class="position-section" v-if="record.decision.position_suggestion">
            <h4>仓位建议</h4>
            <p>{{ record.decision.position_suggestion }}</p>
          </div>

          <div class="risk-section" v-if="record.decision.risk_warning">
            <h4>风险提示</h4>
            <el-alert :title="record.decision.risk_warning" type="warning" :closable="false" show-icon />
          </div>
        </el-card>

        <!-- 交易信号 -->
        <el-card v-if="record.trading_signals?.length" class="signals-card" shadow="never">
          <template #header>
            <span class="card-title">交易信号</span>
          </template>
          <el-table :data="record.trading_signals" border>
            <el-table-column prop="code" label="股票代码" width="120" />
            <el-table-column prop="name" label="股票名称" width="100" />
            <el-table-column label="方向" width="100">
              <template #default="{ row }">
                <el-tag :type="row.action === '买入' ? 'success' : row.action === '卖出' ? 'danger' : 'info'" size="small">
                  {{ row.action }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="价格" width="100">
              <template #default="{ row }">{{ row.price ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="数量" width="100">
              <template #default="{ row }">{{ row.volume ? `${row.volume}股` : '-' }}</template>
            </el-table-column>
            <el-table-column label="金额" width="120">
              <template #default="{ row }">{{ row.amount ? formatMoney(row.amount) : '-' }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
          </el-table>
        </el-card>

        <!-- 下单结果 -->
        <el-card v-if="record.order_results?.length" class="orders-card" shadow="never">
          <template #header>
            <span class="card-title">下单结果</span>
          </template>
          <el-table :data="record.order_results" border>
            <el-table-column prop="code" label="股票代码" width="120" />
            <el-table-column prop="name" label="股票名称" width="100" />
            <el-table-column label="方向" width="80">
              <template #default="{ row }">
                <el-tag :type="row.action === '买入' ? 'success' : 'danger'" size="small">
                  {{ row.action }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="价格" width="100">
              <template #default="{ row }">{{ row.price ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="数量" width="100">
              <template #default="{ row }">{{ row.volume ? `${row.volume}股` : '-' }}</template>
            </el-table-column>
            <el-table-column label="订单号" width="160">
              <template #default="{ row }">{{ row.order_id ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="交易成本" width="100">
              <template #default="{ row }">{{ row.simulated_cost != null ? `¥${row.simulated_cost.toFixed(2)}` : '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="error" label="失败原因" min-width="200" show-overflow-tooltip />
          </el-table>
        </el-card>

        <!-- 各分析师报告 -->
        <el-card v-if="record.analyst_results?.length" class="analysts-card" shadow="never">
          <template #header>
            <span class="card-title">分析师报告</span>
          </template>

          <el-tabs>
            <el-tab-pane
              v-for="(analyst, idx) in record.analyst_results"
              :key="idx"
              :label="analyst.name"
            >
              <div class="analyst-header">
                <el-tag :type="(analyst.tag_type as any)" size="small">
                  {{ analyst.conclusion }}
                </el-tag>
              </div>
              <div class="analyst-content" v-html="renderMarkdown(analyst.content)"></div>
            </el-tab-pane>
          </el-tabs>
        </el-card>

        <!-- 完整决策报告 -->
        <el-card v-if="record.decision_report" class="report-card" shadow="never">
          <template #header>
            <span class="card-title">完整决策报告</span>
          </template>
          <div class="report-content" v-html="renderMarkdown(record.decision_report)"></div>
        </el-card>
      </template>

      <el-empty v-else-if="!loading" description="记录不存在" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { aiTradingApi, type AiTradingResult } from '@/api/aiTrading'
import { formatDateTime } from '@/utils/datetime'
import { marked } from 'marked'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const record = ref<AiTradingResult | null>(null)

const fetchDetail = async () => {
  const taskId = route.params.id as string
  if (!taskId) return

  loading.value = true
  try {
    const res = await aiTradingApi.getRecordDetail(taskId)
    if (res.success && res.data) {
      record.value = res.data
    }
  } catch (e) {
    console.error('获取交易记录详情失败:', e)
    ElMessage.error('获取交易记录详情失败')
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push('/ai-trading/records')

const getActionTagType = (action: string) => {
  if (!action) return 'info'
  if (action.includes('买入') || action.includes('建仓')) return 'success'
  if (action.includes('卖出')) return 'danger'
  if (action.includes('减仓') || action.includes('调仓')) return 'warning'
  return 'info'
}

const getStatusType = (status: string) => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    completed: 'success',
    running: 'warning',
    pending: 'info',
    failed: 'danger',
  }
  return map[status] || 'info'
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = {
    completed: '已完成',
    running: '运行中',
    pending: '等待中',
    failed: '失败',
  }
  return map[status] || status
}

const formatTime = (time: string) => formatDateTime(time)

const formatMoney = (val: number) => `¥${val?.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const remain = Math.round(seconds % 60)
  return remain > 0 ? `${minutes}分${remain}秒` : `${minutes}分`
}

const renderMarkdown = (content: string) => {
  if (!content) return ''
  return marked(content)
}

onMounted(() => fetchDetail())
</script>

<style lang="scss" scoped>
.ai-trading-record-detail {
  .detail-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;

    .header-info {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;

      .header-time,
      .header-elapsed {
        font-size: 14px;
        color: var(--el-text-color-regular);
      }
    }
  }

  .detail-content {
    min-height: 200px;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .card-title {
      font-size: 16px;
      font-weight: 600;
    }
  }

  .card-title {
    font-size: 16px;
    font-weight: 600;
  }

  .account-card {
    margin-bottom: 24px;

    .positions-section {
      margin-top: 16px;

      h4 {
        margin: 0 0 8px;
        font-size: 15px;
        color: var(--el-text-color-primary);
      }
    }
  }

  .decision-card {
    margin-bottom: 24px;

    h4 {
      margin: 16px 0 8px;
      font-size: 15px;
      color: var(--el-text-color-primary);

      &:first-child {
        margin-top: 0;
      }
    }

    .reasoning-text {
      line-height: 1.8;
      color: var(--el-text-color-regular);
    }

    .position-section,
    .risk-section {
      p {
        line-height: 1.8;
        color: var(--el-text-color-regular);
      }
    }
  }

  .signals-card,
  .orders-card {
    margin-bottom: 24px;
  }

  .analysts-card {
    margin-bottom: 24px;

    .analyst-header {
      margin-bottom: 12px;
    }

    .analyst-content {
      line-height: 1.8;
      color: var(--el-text-color-regular);

      :deep(table) {
        border-collapse: collapse;
        width: 100%;
        margin: 8px 0;

        th, td {
          border: 1px solid var(--el-border-color);
          padding: 6px 12px;
          text-align: left;
        }

        th {
          background-color: var(--el-fill-color-light);
        }
      }
    }
  }

  .report-card {
    margin-bottom: 24px;

    .report-content {
      line-height: 1.8;
      color: var(--el-text-color-regular);

      :deep(table) {
        border-collapse: collapse;
        width: 100%;
        margin: 8px 0;

        th, td {
          border: 1px solid var(--el-border-color);
          padding: 6px 12px;
          text-align: left;
        }

        th {
          background-color: var(--el-fill-color-light);
        }
      }
    }
  }
}
</style>
