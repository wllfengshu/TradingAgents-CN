<template>
  <div class="portfolio-page">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="title-section">
          <h1 class="page-title">
            <el-icon class="title-icon"><DataAnalysis /></el-icon>
            持仓收益
          </h1>
          <p class="page-description">跟踪模拟/实盘账户的持仓变化、收益率与风险指标</p>
        </div>
        <div class="header-actions">
          <el-radio-group v-model="mode" size="large" @change="handleModeChange">
            <el-radio-button label="paper">模拟</el-radio-button>
            <el-radio-button label="live">实盘</el-radio-button>
          </el-radio-group>
        </div>
      </div>
    </div>

    <!-- 概览卡片 -->
    <div v-loading="loading" class="overview-cards">
      <el-row :gutter="16">
        <el-col :xs="12" :sm="6">
          <el-card class="stat-card" shadow="hover">
            <div class="stat-label">总资产</div>
            <div class="stat-value">{{ formatMoney(portfolio.total_value) }}</div>
            <div class="stat-sub">
              初始资金: {{ formatMoney(portfolio.initial_capital) }}
            </div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card class="stat-card" shadow="hover">
            <div class="stat-label">总收益</div>
            <div class="stat-value" :class="portfolio.total_return >= 0 ? 'profit' : 'loss'">
              {{ portfolio.total_return >= 0 ? '+' : '' }}{{ formatMoney(portfolio.total_return) }}
            </div>
            <div class="stat-sub">
              收益率:
              <span :class="portfolio.total_return_pct >= 0 ? 'profit' : 'loss'">
                {{ portfolio.total_return_pct >= 0 ? '+' : '' }}{{ portfolio.total_return_pct.toFixed(2) }}%
              </span>
            </div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card class="stat-card" shadow="hover">
            <div class="stat-label">可用资金</div>
            <div class="stat-value">{{ formatMoney(portfolio.cash) }}</div>
            <div class="stat-sub">
              仓位占比: {{ positionRatio }}
            </div>
          </el-card>
        </el-col>
        <el-col :xs="12" :sm="6">
          <el-card class="stat-card" shadow="hover">
            <div class="stat-label">夏普比率</div>
            <div class="stat-value">{{ portfolio.sharpe_ratio }}</div>
            <div class="stat-sub">
              最大回撤: {{ portfolio.max_drawdown_pct.toFixed(2) }}%
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 持仓明细 -->
    <el-card class="section-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <h3>持仓明细</h3>
          <div class="header-badges">
            <el-tag type="info" size="small">{{ portfolio.holdings.length }} 只</el-tag>
            <el-tag v-if="mode === 'paper'" type="warning" size="small" style="margin-left: 8px;">模拟</el-tag>
            <el-tag v-else type="danger" size="small" style="margin-left: 8px;">实盘</el-tag>
          </div>
        </div>
      </template>

      <el-table
        v-if="portfolio.holdings.length > 0"
        :data="portfolio.holdings"
        size="default"
        class="holdings-table"
        :default-sort="{ prop: 'market_value', order: 'descending' }"
      >
        <el-table-column prop="code" label="代码" width="120" />
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="volume" label="持仓(股)" width="100" sortable />
        <el-table-column prop="cost_price" label="成本价" width="100">
          <template #default="{ row }">{{ row.cost_price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="current_price" label="现价" width="100">
          <template #default="{ row }">{{ row.current_price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="market_value" label="市值" width="130" sortable>
          <template #default="{ row }">{{ formatMoney(row.market_value) }}</template>
        </el-table-column>
        <el-table-column label="盈亏" width="130" sortable sort-by="pnl">
          <template #default="{ row }">
            <span :class="row.pnl >= 0 ? 'profit' : 'loss'">
              {{ row.pnl >= 0 ? '+' : '' }}{{ formatMoney(row.pnl) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="收益率" min-width="110" sortable sort-by="pnl_pct">
          <template #default="{ row }">
            <el-tag
              :type="row.pnl_pct >= 0 ? 'danger' : 'success'"
              size="small"
              effect="dark"
            >
              {{ row.pnl_pct >= 0 ? '+' : '' }}{{ row.pnl_pct.toFixed(2) }}%
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-else description="暂无持仓" />
    </el-card>

    <!-- 收益曲线 + 风险指标 -->
    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :xs="24" :lg="16">
        <el-card class="section-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <h3>净值曲线</h3>
              <el-select v-model="historyDays" size="small" style="width: 120px;" @change="loadHistory">
                <el-option label="近7天" :value="7" />
                <el-option label="近30天" :value="30" />
                <el-option label="近90天" :value="90" />
                <el-option label="近1年" :value="365" />
              </el-select>
            </div>
          </template>

          <div v-if="historyData.nav_curve.length > 1" class="nav-chart">
            <div class="chart-container">
              <div
                v-for="(point, idx) in historyData.nav_curve"
                :key="idx"
                class="chart-bar-wrapper"
                :style="{ width: barWidth + '%' }"
              >
                <el-tooltip
                  :content="`${point.date}\n净值: ${formatMoney(point.nav)}\n收益率: ${point.return_pct.toFixed(2)}%`"
                  placement="top"
                >
                  <div
                    class="chart-bar"
                    :class="point.return_pct >= 0 ? 'bar-profit' : 'bar-loss'"
                    :style="{ height: barHeight(point) + 'px' }"
                  />
                </el-tooltip>
                <div v-if="idx % Math.max(1, Math.floor(historyData.nav_curve.length / 8)) === 0" class="chart-label">
                  {{ point.date.slice(5) }}
                </div>
              </div>
            </div>
          </div>
          <el-empty v-else description="暂无净值数据，运行AI交易后自动生成" :image-size="80" />
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="8">
        <el-card class="section-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <h3>风险指标</h3>
            </div>
          </template>

          <div class="risk-metrics">
            <div class="metric-row">
              <span class="metric-label">夏普比率</span>
              <span class="metric-value" :class="portfolio.sharpe_ratio >= 1 ? 'profit' : portfolio.sharpe_ratio >= 0 ? '' : 'loss'">
                {{ portfolio.sharpe_ratio }}
              </span>
            </div>
            <div class="metric-row">
              <span class="metric-label">最大回撤</span>
              <span class="metric-value loss">-{{ portfolio.max_drawdown_pct.toFixed(2) }}%</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">胜率</span>
              <span class="metric-value" :class="portfolio.win_rate >= 50 ? 'profit' : 'loss'">
                {{ portfolio.win_rate.toFixed(1) }}%
              </span>
            </div>
            <div class="metric-row">
              <span class="metric-label">总收益率</span>
              <span class="metric-value" :class="portfolio.total_return_pct >= 0 ? 'profit' : 'loss'">
                {{ portfolio.total_return_pct >= 0 ? '+' : '' }}{{ portfolio.total_return_pct.toFixed(2) }}%
              </span>
            </div>
            <el-divider />
            <div class="metric-row">
              <span class="metric-label">持仓数量</span>
              <span class="metric-value">{{ portfolio.holdings.length }} 只</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">持仓市值</span>
              <span class="metric-value">{{ formatMoney(portfolio.total_value - portfolio.cash) }}</span>
            </div>
            <div class="metric-row">
              <span class="metric-label">可用资金</span>
              <span class="metric-value">{{ formatMoney(portfolio.cash) }}</span>
            </div>
          </div>

          <el-divider />

          <div class="init-section">
            <el-button
              v-if="mode === 'paper'"
              type="warning"
              size="small"
              @click="handleInitPortfolio"
            >
              重置模拟账户
            </el-button>
            <p class="init-hint">
              {{ mode === 'paper' ? '重置将清空模拟持仓，初始资金恢复为100万' : '实盘数据来自真实账户' }}
            </p>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近交易 -->
    <el-card class="section-card" shadow="hover" style="margin-top: 16px;">
      <template #header>
        <div class="card-header">
          <h3>最近交易</h3>
          <el-tag type="info" size="small">{{ portfolio.recent_orders.length }} 笔</el-tag>
        </div>
      </template>

      <el-table
        v-if="portfolio.recent_orders.length > 0"
        :data="portfolio.recent_orders"
        size="small"
      >
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="code" label="代码" width="120" />
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="action" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.action === '买入' ? 'danger' : 'success'" size="small" effect="dark">
              {{ row.action }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格" width="100">
          <template #default="{ row }">{{ row.price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column prop="volume" label="数量" width="80" />
        <el-table-column prop="amount" label="金额" width="130">
          <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="simulated_cost" label="交易成本" min-width="110">
          <template #default="{ row }">{{ row.simulated_cost?.toFixed(2) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无交易记录" :image-size="80" />
    </el-card>

    <!-- 无数据状态 -->
    <div v-if="!loading && !portfolio.has_data" class="empty-state">
      <el-empty description="暂无持仓数据，请先运行AI交易任务">
        <el-button type="primary" @click="$router.push('/ai-trading/home')">
          前往交易
        </el-button>
      </el-empty>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataAnalysis } from '@element-plus/icons-vue'
import { aiTradingApi, type AiTradingMode, type AiTradingPortfolio, type AiTradingPortfolioHistory } from '@/api/aiTrading'

const mode = ref<AiTradingMode>('paper')
const loading = ref(false)
const historyDays = ref(30)

const portfolio = reactive<AiTradingPortfolio>({
  mode: 'paper',
  cash: 0,
  total_value: 0,
  initial_capital: 0,
  total_return: 0,
  total_return_pct: 0,
  holdings: [],
  daily_returns: [],
  sharpe_ratio: 0,
  max_drawdown: 0,
  max_drawdown_pct: 0,
  win_rate: 0,
  recent_orders: [],
  has_data: false,
})

const historyData = reactive<AiTradingPortfolioHistory>({
  mode: 'paper',
  nav_curve: [],
  trade_calendar: [],
  has_data: false,
})

const positionRatio = computed(() => {
  if (!portfolio.total_value || portfolio.total_value === 0) return '0%'
  const holdingValue = portfolio.total_value - portfolio.cash
  return ((holdingValue / portfolio.total_value) * 100).toFixed(1) + '%'
})

const barWidth = computed(() => {
  const count = historyData.nav_curve.length
  if (count <= 1) return 100
  return Math.max(2, Math.min(100 / count, 20))
})

function barHeight(point: { return_pct: number }) {
  const maxAbs = Math.max(
    ...historyData.nav_curve.map(p => Math.abs(p.return_pct)),
    1
  )
  return Math.max(4, (Math.abs(point.return_pct) / maxAbs) * 150)
}

function formatMoney(value: number | undefined | null): string {
  if (value === undefined || value === null) return '-'
  return value.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
}

function formatDateTime(value: string): string {
  if (!value) return '-'
  try {
    const d = new Date(value)
    if (isNaN(d.getTime())) return value
    return d.toLocaleString('zh-CN')
  } catch {
    return value
  }
}

async function loadPortfolio() {
  loading.value = true
  try {
    const res = await aiTradingApi.getPortfolio(mode.value)
    if (res.success && res.data) {
      Object.assign(portfolio, res.data)
    }
  } catch (error: any) {
    console.error('获取持仓数据失败:', error)
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  try {
    const res = await aiTradingApi.getPortfolioHistory(mode.value, historyDays.value)
    if (res.success && res.data) {
      Object.assign(historyData, res.data)
    }
  } catch (error: any) {
    console.error('获取历史数据失败:', error)
  }
}

function handleModeChange() {
  loadPortfolio()
  loadHistory()
}

async function handleInitPortfolio() {
  try {
    await ElMessageBox.confirm(
      '重置将清空所有模拟持仓和交易记录，初始资金恢复为100万，确认继续？',
      '重置确认',
      { confirmButtonText: '确认重置', cancelButtonText: '取消', type: 'warning' }
    )

    const res = await aiTradingApi.initPaperPortfolio(1000000)
    if (res.success) {
      ElMessage.success('模拟账户已重置')
      loadPortfolio()
      loadHistory()
    }
  } catch {
    // 用户取消
  }
}

onMounted(() => {
  loadPortfolio()
  loadHistory()
})
</script>

<style lang="scss" scoped>
.portfolio-page {
  min-height: 100vh;
  background: var(--el-bg-color-page);
  padding: 24px;

  .page-header {
    margin-bottom: 24px;

    .header-content {
      background: var(--el-bg-color);
      padding: 24px 32px;
      border-radius: 16px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .title-section {
      .page-title {
        display: flex;
        align-items: center;
        font-size: 28px;
        font-weight: 700;
        color: #1a202c;
        margin: 0 0 6px 0;

        .title-icon {
          margin-right: 10px;
          color: #3b82f6;
        }
      }

      .page-description {
        font-size: 14px;
        color: #64748b;
        margin: 0;
      }
    }
  }

  .overview-cards {
    margin-bottom: 16px;

    .stat-card {
      border-radius: 12px;
      border: none;
      box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
      text-align: center;
      margin-bottom: 8px;

      :deep(.el-card__body) {
        padding: 20px 16px;
      }

      .stat-label {
        font-size: 13px;
        color: #64748b;
        margin-bottom: 8px;
      }

      .stat-value {
        font-size: 22px;
        font-weight: 700;
        color: #1a202c;

        &.profit { color: #f56c6c; }
        &.loss { color: #67c23a; }
      }

      .stat-sub {
        font-size: 12px;
        color: #94a3b8;
        margin-top: 6px;

        .profit { color: #f56c6c; }
        .loss { color: #67c23a; }
      }
    }
  }

  .section-card {
    border-radius: 12px;
    border: none;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);

    :deep(.el-card__header) {
      padding: 16px 20px;
      border-bottom: 1px solid #f0f0f0;
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;

      h3 {
        margin: 0;
        font-size: 16px;
        font-weight: 600;
        color: #1a202c;
      }

      .header-badges {
        display: flex;
        align-items: center;
      }
    }
  }

  .profit { color: #f56c6c; }
  .loss { color: #67c23a; }

  // 净值曲线
  .nav-chart {
    padding: 16px 0;

    .chart-container {
      display: flex;
      align-items: flex-end;
      height: 180px;
      gap: 2px;
      padding: 0 8px;
      border-bottom: 1px solid #e2e8f0;
    }

    .chart-bar-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-end;
      height: 100%;
      min-width: 4px;
    }

    .chart-bar {
      width: 100%;
      min-height: 4px;
      border-radius: 2px 2px 0 0;
      cursor: pointer;
      transition: opacity 0.2s;

      &:hover { opacity: 0.8; }

      &.bar-profit { background: linear-gradient(180deg, #f56c6c, #fab6b6); }
      &.bar-loss { background: linear-gradient(180deg, #67c23a, #b3e19d); }
    }

    .chart-label {
      font-size: 10px;
      color: #94a3b8;
      margin-top: 4px;
      white-space: nowrap;
    }
  }

  // 风险指标
  .risk-metrics {
    .metric-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 0;
      border-bottom: 1px solid #f8fafc;

      &:last-child { border-bottom: none; }

      .metric-label {
        font-size: 13px;
        color: #64748b;
      }

      .metric-value {
        font-size: 15px;
        font-weight: 600;
        color: #1a202c;

        &.profit { color: #f56c6c; }
        &.loss { color: #67c23a; }
      }
    }
  }

  .init-section {
    text-align: center;

    .init-hint {
      font-size: 11px;
      color: #94a3b8;
      margin-top: 8px;
    }
  }

  .empty-state {
    padding: 60px 0;
  }
}
</style>
