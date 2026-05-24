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
            AI驱动的智能交易系统，多Agent协同分析持仓与机会，自动生成买卖信号
          </p>
        </div>
      </div>
    </div>

    <!-- 主要内容区 -->
    <div class="trading-container">
      <!-- Agent团队 -->
      <el-card class="team-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <h3>交易Agent团队</h3>
            <el-tag type="info" size="small">协同交易</el-tag>
          </div>
        </template>

        <div class="analysts-grid">
          <div
            v-for="analyst in analystTeam"
            :key="analyst.name"
            class="analyst-card"
          >
            <div class="analyst-avatar" :style="{ background: analyst.bgColor }">
              <span class="analyst-emoji">{{ analyst.emoji }}</span>
            </div>
            <div class="analyst-content">
              <div class="analyst-name">{{ analyst.name }}</div>
              <div class="analyst-desc">{{ analyst.description }}</div>
              <div class="analyst-tags">
                <el-tag
                  v-for="tag in analyst.tags"
                  :key="tag"
                  size="small"
                  effect="plain"
                  class="analyst-tag"
                >
                  {{ tag }}
                </el-tag>
              </div>
            </div>
          </div>
        </div>

        <!-- 协同流程说明 -->
        <div class="flow-section">
          <h4 class="section-title">协同流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-number">1</div>
              <div class="step-text">查询账户</div>
              <div class="step-condition">资金+持仓（模拟数据）</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">2</div>
              <div class="step-text">分析阶段</div>
              <div class="step-condition">有持仓：股票分析+AI选股 并行<br/>无持仓：仅 AI选股</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">3</div>
              <div class="step-text">仓位管理分析师</div>
              <div class="step-condition">综合持仓/分析/选股，给出买卖信号</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">4</div>
              <div class="step-text">交易决策分析师</div>
              <div class="step-condition">审核信号，执行下单</div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 交易控制 -->
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
            <el-radio-button label="paper">
              <el-icon><Monitor /></el-icon>
              模拟模式
            </el-radio-button>
            <el-radio-button label="live">
              <el-icon><Promotion /></el-icon>
              实盘模式
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

      <!-- 运行结果 -->
      <div v-if="hasResult" class="results-section">
        <el-card class="results-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <h3>运行结果</h3>
              <div class="result-meta">
                <el-tag :type="tradingMode === 'live' ? 'danger' : 'success'" size="small">
                  {{ tradingMode === 'live' ? '实盘' : '模拟' }}
                </el-tag>
                <el-tag type="success" size="small" style="margin-left: 8px;">{{ resultTime }}</el-tag>
              </div>
            </div>
          </template>

          <!-- 风险提示 -->
          <div class="risk-disclaimer">
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>
                <span style="font-weight: bold;">本系统为AI辅助交易工具，分析结果仅供参考，不构成任何投资建议。市场有风险，投资需谨慎。</span>
              </template>
            </el-alert>
          </div>

          <!-- 持仓信息 -->
          <div v-if="resultData.accountInfo" class="account-section">
            <h4 class="section-title">账户信息</h4>
            <div class="account-card">
              <div class="account-item">
                <span class="label">可用资金：</span>
                <span class="value">{{ formatMoney(resultData.accountInfo.cash) }}</span>
              </div>
              <div class="account-item">
                <span class="label">总资产：</span>
                <span class="value">{{ formatMoney(resultData.accountInfo.total_value) }}</span>
              </div>
              <div class="account-item">
                <span class="label">冻结资金：</span>
                <span class="value">{{ formatMoney(resultData.accountInfo.frozen_cash) }}</span>
              </div>
            </div>

            <div v-if="resultData.positions && resultData.positions.length > 0" class="positions-section">
              <h4 class="section-title" style="margin-top: 16px;">当前持仓</h4>
              <el-table :data="resultData.positions" size="small" class="positions-table">
                <el-table-column prop="code" label="代码" width="120" />
                <el-table-column prop="name" label="名称" width="100" />
                <el-table-column prop="volume" label="数量(股)" width="100" />
                <el-table-column prop="cost_price" label="成本价" width="100">
                  <template #default="{ row }">{{ row.cost_price?.toFixed(2) }}</template>
                </el-table-column>
                <el-table-column prop="current_price" label="现价" width="100">
                  <template #default="{ row }">{{ row.current_price?.toFixed(2) }}</template>
                </el-table-column>
                <el-table-column label="市值" width="120">
                  <template #default="{ row }">{{ formatMoney(row.volume * row.current_price) }}</template>
                </el-table-column>
                <el-table-column label="盈亏" min-width="120">
                  <template #default="{ row }">
                    <span :style="{ color: getProfitColor(row.current_price - row.cost_price) }">
                      {{ formatMoney((row.current_price - row.cost_price) * row.volume) }}
                    </span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <div v-else class="no-position">
              <el-tag type="info">空仓</el-tag>
            </div>
          </div>

          <!-- 各分析师结论 -->
          <div v-if="resultData.analystResults && resultData.analystResults.length > 0" class="analyst-results">
            <h4 class="section-title">各分析师结论</h4>
            <el-tabs v-model="activeResultTab" type="card">
              <el-tab-pane
                v-for="(result, index) in resultData.analystResults"
                :key="index"
                :label="result.name"
                :name="String(index)"
              >
                <div class="result-pane">
                  <div class="result-summary">
                    <el-tag :type="result.tagType" size="large">{{ result.conclusion }}</el-tag>
                  </div>
                  <div class="result-detail" v-html="formatContent(result.content)"></div>
                </div>
              </el-tab-pane>
            </el-tabs>
          </div>

          <!-- 交易信号 -->
          <div v-if="resultData.tradingSignals && resultData.tradingSignals.length > 0" class="signals-section">
            <h4 class="section-title">交易信号</h4>
            <el-table :data="resultData.tradingSignals" size="small" class="signals-table">
              <el-table-column prop="code" label="股票代码" width="120" />
              <el-table-column prop="name" label="股票名称" width="100" />
              <el-table-column prop="action" label="方向" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.action === '买入' ? 'success' : 'danger'" size="small" effect="dark">
                    {{ row.action }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="price" label="价格" width="100">
                <template #default="{ row }">{{ row.price?.toFixed(2) }}</template>
              </el-table-column>
              <el-table-column prop="volume" label="数量(股)" width="100" />
              <el-table-column prop="amount" label="金额" width="120">
                <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
              </el-table-column>
              <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
            </el-table>
          </div>

          <!-- 下单结果 -->
          <div v-if="resultData.orderResults && resultData.orderResults.length > 0" class="orders-section">
            <h4 class="section-title">下单结果</h4>
            <div class="orders-list">
              <div
                v-for="(order, index) in resultData.orderResults"
                :key="index"
                class="order-item"
                :class="{ 'order-success': order.success, 'order-failed': !order.success }"
              >
                <div class="order-header">
                  <el-tag :type="order.action === '买入' ? 'success' : 'danger'" size="small" effect="dark">
                    {{ order.action }}
                  </el-tag>
                  <span class="order-code">{{ order.code }} {{ order.name }}</span>
                  <el-tag :type="order.success ? 'success' : 'danger'" size="small">
                    {{ order.success ? '成功' : '失败' }}
                  </el-tag>
                </div>
                <div class="order-detail">
                  <span v-if="order.order_id">订单号：{{ order.order_id }}</span>
                  <span v-if="order.price">价格：{{ order.price?.toFixed(2) }}</span>
                  <span v-if="order.volume">数量：{{ order.volume }}股</span>
                  <span v-if="order.error" class="order-error">{{ order.error }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 决策摘要 -->
          <div v-if="resultData.decision" class="decision-section">
            <h4 class="section-title">综合决策</h4>
            <div class="decision-card">
              <div class="decision-action">
                <span class="label">决策倾向：</span>
                <el-tag
                  :type="getActionTagType(resultData.decision.action)"
                  size="large"
                  effect="dark"
                >
                  {{ resultData.decision.action }}
                </el-tag>
              </div>
              <div class="decision-reasoning" v-if="resultData.decision.reasoning">
                <span class="label">决策依据：</span>
                <p>{{ resultData.decision.reasoning }}</p>
              </div>
              <div class="decision-position" v-if="resultData.decision.position_suggestion">
                <span class="label">仓位建议：</span>
                <p>{{ resultData.decision.position_suggestion }}</p>
              </div>
              <div class="decision-risk" v-if="resultData.decision.risk_warning">
                <span class="label">风险提示：</span>
                <p>{{ resultData.decision.risk_warning }}</p>
              </div>
            </div>
          </div>

          <!-- 操作按钮 -->
          <div class="result-actions">
            <el-button type="primary" @click="handleExport">
              <el-icon><Download /></el-icon>
              导出结果
            </el-button>
            <el-button @click="handleReset">
              <el-icon><Refresh /></el-icon>
              重新运行
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- 空状态 -->
      <div v-if="!hasResult && !running" class="empty-section">
        <el-empty description="暂无运行结果，请点击运行按钮开始AI交易">
          <template #image>
            <div class="empty-icon">
              <el-icon :size="64" color="#c0c4cc"><TrendCharts /></el-icon>
            </div>
          </template>
        </el-empty>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  TrendCharts,
  VideoPlay,
  VideoPause,
  Promotion,
  Monitor,
  ArrowRight,
  Download,
  Refresh,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import { aiTradingApi } from '@/api/aiTrading'

marked.setOptions({
  breaks: true,
  gfm: true
})

// Agent团队定义
const analystTeam = [
  {
    name: '账户与持仓查询',
    emoji: '💼',
    description: '获取账户信息（资金、持仓），根据是否有持仓决定后续流程分支（当前使用模拟数据）',
    tags: ['账户信息', '持仓查询', '流程分支'],
    bgColor: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
  },
  {
    name: '股票分析',
    emoji: '📊',
    description: '当持仓不为空时，对每只持仓股票进行多维度分析（基本面/技术面/消息面），为后续仓位管理提供依据',
    tags: ['持仓评估', '基本面', '技术面', '消息面'],
    bgColor: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)'
  },
  {
    name: 'AI选股',
    emoji: '🎯',
    description: '多Agent协同筛选优质新机会；有持仓时与"股票分析"并行执行，无持仓时单独执行',
    tags: ['大盘分析', '主线板块', '龙头筛选', '风险评估'],
    bgColor: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)'
  },
  {
    name: '仓位管理分析师',
    emoji: '⚖️',
    description: '综合持仓信息、股票分析结果、AI选股结果（或无持仓时仅资金+选股结果），给出具体买卖信号',
    tags: ['买卖信号', '仓位调整', '风险控制'],
    bgColor: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)'
  },
  {
    name: '交易决策分析师',
    emoji: '🚀',
    description: '审核仓位管理分析师给出的买卖信号，确认后执行下单',
    tags: ['信号审核', '风控检查', '下单执行'],
    bgColor: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)'
  }
]

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

// 结果数据
const hasResult = ref(false)
const resultTime = ref('')
const activeResultTab = ref('0')
const resultData = reactive<{
  accountInfo: {
    cash: number
    total_value: number
    frozen_cash: number
  } | null
  positions: Array<{
    code: string
    name: string
    volume: number
    cost_price: number
    current_price: number
  }>
  analystResults: Array<{
    name: string
    conclusion: string
    tagType: 'success' | 'warning' | 'danger' | 'info'
    content: string
  }>
  tradingSignals: Array<{
    code: string
    name: string
    action: string
    price: number
    volume: number
    amount: number
    reason: string
  }>
  orderResults: Array<{
    code: string
    name: string
    action: string
    price: number
    volume: number
    order_id: string | null
    success: boolean
    error: string | null
  }>
  decision: {
    action: string
    reasoning: string
    position_suggestion?: string
    risk_warning?: string
  } | null
  earlyStop: boolean
  earlyStopReason: string
}>({
  accountInfo: null,
  positions: [],
  analystResults: [],
  tradingSignals: [],
  orderResults: [],
  decision: null,
  earlyStop: false,
  earlyStopReason: ''
})

// 格式化时间
const formatTime = (seconds: number): string => {
  if (!seconds || seconds <= 0) return '0秒'
  if (seconds < 60) return `${Math.floor(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  return remainingSeconds > 0 ? `${minutes}分${remainingSeconds}秒` : `${minutes}分钟`
}

// 格式化金额
const formatMoney = (value: number | undefined | null): string => {
  if (value === undefined || value === null) return '-'
  return value.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })
}

// 盈亏颜色
const getProfitColor = (profit: number): string => {
  if (profit > 0) return '#f56c6c'
  if (profit < 0) return '#67c23a'
  return '#909399'
}

// 格式化内容（Markdown -> HTML）
const formatContent = (content: string): string => {
  if (!content) return ''
  try {
    return marked.parse(content) as string
  } catch {
    return `<pre style="white-space: pre-wrap;">${content}</pre>`
  }
}

// 获取操作标签类型
const getActionTagType = (action: string): 'success' | 'warning' | 'danger' | 'info' => {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    '强烈推荐买入': 'success',
    '谨慎买入': 'success',
    '建议卖出': 'danger',
    '减仓': 'warning',
    '观望': 'info',
    '空仓': 'info',
  }
  return map[action] || 'info'
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

        await fetchResult()
        ElMessage.success('AI交易任务完成')
      } else if (task.status === 'failed') {
        if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
        if (timer) { clearInterval(timer); timer = null }

        progressStatus.value = 'exception'
        currentStep.value = '交易失败'
        running.value = false

        const errMsg = task.error_message || '交易过程中发生错误'
        ElMessage.error(errMsg)
      }
    } catch (error) {
      console.error('轮询AI交易任务状态失败:', error)
    }
  }, 3000)
}

// 获取完整结果
const fetchResult = async () => {
  if (!currentTaskId.value) return

  try {
    const res = await aiTradingApi.getResult(currentTaskId.value)
    if (!res.success || !res.data) return

    const data = res.data
    hasResult.value = true
    resultTime.value = new Date().toLocaleString('zh-CN')
    activeResultTab.value = '0'

    // 填充账户信息
    if (data.account_info) {
      resultData.accountInfo = data.account_info
    }

    // 填充持仓
    if (data.positions && Array.isArray(data.positions)) {
      resultData.positions = data.positions
    }

    // 填充分析师结果
    if (data.analyst_results && Array.isArray(data.analyst_results)) {
      resultData.analystResults = data.analyst_results.map((r: any) => ({
        name: r.name,
        conclusion: r.conclusion,
        tagType: r.tag_type as 'success' | 'warning' | 'danger' | 'info',
        content: r.content,
      }))
    }

    // 填充提前终止信息
    resultData.earlyStop = !!data.early_stop
    resultData.earlyStopReason = data.early_stop_reason || ''

    // 填充交易信号
    if (data.trading_signals && Array.isArray(data.trading_signals)) {
      resultData.tradingSignals = data.trading_signals
    }

    // 填充下单结果
    if (data.order_results && Array.isArray(data.order_results)) {
      resultData.orderResults = data.order_results
    }

    // 填充决策结果
    if (data.decision) {
      resultData.decision = data.decision
    }
  } catch (error) {
    console.error('获取AI交易结果失败:', error)
    ElMessage.error('获取结果失败')
  }
}

// 导出结果
const handleExport = () => {
  if (!hasResult.value) {
    ElMessage.warning('暂无结果可导出')
    return
  }
  const data = JSON.stringify(resultData, null, 2)
  const blob = new Blob([data], { type: 'application/json' })
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `AI交易结果_${new Date().toISOString().slice(0, 10)}.json`
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
  ElMessage.success('结果导出成功')
}

// 重新运行
const handleReset = () => {
  hasResult.value = false
  resultData.accountInfo = null
  resultData.positions = []
  resultData.analystResults = []
  resultData.tradingSignals = []
  resultData.orderResults = []
  resultData.decision = null
  resultData.earlyStop = false
  resultData.earlyStopReason = ''
  progress.value = 0
  elapsedTime.value = 0
  currentStep.value = ''
  progressStatus.value = ''
  currentTaskId.value = ''
}

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
    .team-card,
    .control-card,
    .results-card {
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

          .result-meta {
            display: flex;
            align-items: center;
            color: white;
          }
        }
      }

      :deep(.el-card__body) {
        padding: 24px;
      }
    }

    // 分析师团队网格
    .analysts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-bottom: 24px;

      .analyst-card {
        display: flex;
        align-items: flex-start;
        padding: 20px;
        border: 2px solid #e2e8f0;
        border-radius: 12px;
        transition: all 0.3s ease;

        &:hover {
          border-color: #3b82f6;
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(59, 130, 246, 0.15);
        }

        .analyst-avatar {
          width: 52px;
          height: 52px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          margin-right: 16px;
          flex-shrink: 0;

          .analyst-emoji {
            font-size: 24px;
          }
        }

        .analyst-content {
          flex: 1;
          min-width: 0;

          .analyst-name {
            font-size: 15px;
            font-weight: 600;
            color: #1a202c;
            margin-bottom: 4px;
          }

          .analyst-desc {
            font-size: 12px;
            color: #64748b;
            margin-bottom: 8px;
          }

          .analyst-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;

            .analyst-tag {
              font-size: 11px;
              border-radius: 4px;
            }
          }
        }
      }
    }

    // 协同流程
    .flow-section {
      padding-top: 20px;
      border-top: 1px solid #f0f0f0;

      .section-title {
        font-size: 16px;
        font-weight: 600;
        color: #1a202c;
        margin: 0 0 16px 0;
      }

      .flow-steps {
        display: flex;
        align-items: flex-start;
        justify-content: center;
        gap: 10px;
        flex-wrap: wrap;

        .flow-step {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 6px;

          .step-number {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 16px;
          }

          .step-text {
            font-size: 13px;
            font-weight: 500;
            color: #374151;
          }

          .step-condition {
            font-size: 11px;
            color: #e6a23c;
            white-space: nowrap;
          }
        }

        .flow-arrow {
          color: #c0c4cc;
          font-size: 20px;
          margin-top: 12px;
        }
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

    // 结果区域通用
    .section-title {
      font-size: 16px;
      font-weight: 600;
      color: #1a202c;
      margin: 0 0 16px 0;
      padding-bottom: 8px;
      border-bottom: 2px solid #e2e8f0;
    }

    .risk-disclaimer {
      margin-bottom: 20px;
    }

    // 账户信息
    .account-section {
      margin-bottom: 24px;

      .account-card {
        display: flex;
        gap: 32px;
        padding: 16px;
        background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
        border-radius: 12px;

        .account-item {
          .label {
            font-size: 13px;
            color: #64748b;
          }
          .value {
            font-size: 20px;
            font-weight: 700;
            color: #1e40af;
            margin-left: 4px;
          }
        }
      }

      .no-position {
        margin-top: 12px;
        text-align: center;
        padding: 16px;
        background: #f8fafc;
        border-radius: 12px;
      }
    }

    // 分析师结论
    .analyst-results {
      margin-bottom: 24px;

      .result-pane {
        padding: 16px 0;

        .result-summary {
          margin-bottom: 16px;
        }

        .result-detail {
          :deep(h2) {
            font-size: 16px;
            font-weight: 600;
            color: #1a202c;
            margin: 0 0 12px 0;
          }

          :deep(p) {
            font-size: 14px;
            line-height: 1.8;
            color: #374151;
            margin: 0 0 8px 0;
          }

          :deep(ul) {
            padding-left: 20px;
            margin: 0;
          }

          :deep(li) {
            font-size: 14px;
            line-height: 1.8;
            color: #374151;
          }

          :deep(strong) {
            color: #1a202c;
          }
        }
      }
    }

    // 交易信号
    .signals-section {
      margin-bottom: 24px;
    }

    // 下单结果
    .orders-section {
      margin-bottom: 24px;

      .orders-list {
        display: flex;
        flex-direction: column;
        gap: 12px;

        .order-item {
          padding: 16px;
          border-radius: 12px;
          border: 1px solid #e2e8f0;

          &.order-success {
            background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
            border-color: #86efac;
          }

          &.order-failed {
            background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%);
            border-color: #fca5a5;
          }

          .order-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;

            .order-code {
              font-weight: 600;
              font-size: 15px;
              color: #1a202c;
            }
          }

          .order-detail {
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            font-size: 13px;
            color: #64748b;

            .order-error {
              color: #ef4444;
              font-weight: 500;
            }
          }
        }
      }
    }

    // 决策区域
    .decision-section {
      margin-bottom: 24px;

      .decision-card {
        background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
        border-radius: 12px;
        padding: 24px;

        .label {
          font-weight: 600;
          color: #1e40af;
          margin-right: 8px;
        }

        .decision-action {
          margin-bottom: 16px;
          display: flex;
          align-items: center;
        }

        .decision-reasoning,
        .decision-position,
        .decision-risk {
          margin-top: 12px;

          p {
            margin: 8px 0 0;
            font-size: 14px;
            line-height: 1.8;
            color: #374151;
          }
        }
      }
    }

    // 操作按钮
    .result-actions {
      display: flex;
      gap: 12px;
      padding-top: 16px;
      border-top: 1px solid #e2e8f0;
    }

    // 空状态
    .empty-section {
      padding: 60px 0;

      .empty-icon {
        margin-bottom: 16px;
      }
    }
  }
}
</style>
