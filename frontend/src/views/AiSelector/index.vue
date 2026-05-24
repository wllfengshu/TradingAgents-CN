<template>
  <div class="ai-selector">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="title-section">
          <h1 class="page-title">
            <el-icon class="title-icon"><Cpu /></el-icon>
            AI选股-立即选股
          </h1>
          <p class="page-description">
            AI分析师团队协同工作，多维度智能筛选优质标的
          </p>
        </div>
      </div>
    </div>

    <!-- 主要内容区 -->
    <div class="selector-container">
      <!-- 分析师团队 -->
      <el-card class="team-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <h3>分析师团队</h3>
            <el-tag type="info" size="small">协同分析</el-tag>
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
          <h4 class="section-title">协同流程（逐级筛选，条件终止）</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-number">1</div>
              <div class="step-text">大盘分析</div>
              <div class="step-condition">偏空则终止</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">2</div>
              <div class="step-text">板块识别</div>
              <div class="step-condition">无主线则终止</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">3</div>
              <div class="step-text">合力筛选</div>
              <div class="step-condition">无标的则终止</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">4</div>
              <div class="step-text">龙头确认</div>
              <div class="step-condition">无龙头则终止</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">5</div>
              <div class="step-text">风险评估</div>
              <div class="step-condition">高风险则终止</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">6</div>
              <div class="step-text">综合决策</div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 运行按钮 -->
      <el-card class="action-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <h3>运行控制</h3>
          </div>
        </template>

        <div class="action-buttons">
          <el-button
            type="primary"
            size="large"
            :loading="running"
            :disabled="running"
            class="run-btn"
            @click="handleRunNow"
          >
            <el-icon><VideoPlay /></el-icon>
            {{ running ? '运行中...' : '立即运行' }}
          </el-button>

          <el-button
            size="large"
            class="schedule-btn"
            :class="{ 'has-schedule': !!currentSchedule }"
            @click="handleOpenScheduleDialog"
          >
            <el-icon><Clock /></el-icon>
            {{ currentSchedule ? '已设定时' : '定时运行' }}
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
                <el-tag type="success" size="small">{{ resultTime }}</el-tag>
              </div>
            </div>
          </template>

          <!-- 风险提示 -->
          <div class="risk-disclaimer">
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>
                <span style="font-weight: bold;">本系统为AI辅助选股工具，分析结果仅供参考，不构成任何投资建议。市场有风险，投资需谨慎。</span>
              </template>
            </el-alert>
          </div>

          <!-- 提前终止提示 -->
          <div v-if="resultData.earlyStop" class="early-stop-section">
            <el-alert type="info" :closable="false" show-icon>
              <template #title>
                <span style="font-weight: bold;">分析提前终止：{{ resultData.earlyStopReason }}</span>
              </template>
              <template #default>
                <span>上游分析师判断当前市场条件不满足继续分析的要求，后续步骤已自动跳过。</span>
              </template>
            </el-alert>
          </div>

          <!-- 各分析师结论 -->
          <div v-if="resultData.analystResults" class="analyst-results">
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

          <!-- 决策分析师最终结论 -->
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
              <div class="decision-stocks" v-if="resultData.decision.stocks && resultData.decision.stocks.length > 0">
                <span class="label">推荐标的：</span>
                <div class="stock-tags">
                  <el-tag
                    v-for="stock in resultData.decision.stocks"
                    :key="stock.code"
                    type="success"
                    effect="plain"
                    class="stock-tag"
                  >
                    {{ stock.code }} {{ stock.name }}
                  </el-tag>
                </div>
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
        <el-empty description="暂无运行结果，请点击运行按钮开始AI选股分析">
          <template #image>
            <div class="empty-icon">
              <el-icon :size="64" color="#c0c4cc"><Cpu /></el-icon>
            </div>
          </template>
        </el-empty>
      </div>
    </div>

    <!-- 定时运行对话框 -->
    <el-dialog
      v-model="showScheduleDialog"
      title="定时运行设置"
      width="560px"
      :close-on-click-modal="false"
    >
      <!-- 当前定时任务状态 -->
      <div v-if="currentSchedule" class="current-schedule">
        <el-alert type="success" :closable="false" show-icon>
          <template #title>
            <span>当前已设置定时任务</span>
          </template>
          <template #default>
            <div class="schedule-info">
              <span>Cron表达式：<code>{{ currentSchedule.cron_expression }}</code></span>
              <span v-if="currentSchedule.next_run_time">
                下次运行：{{ currentSchedule.next_run_time }}
              </span>
            </div>
          </template>
        </el-alert>
      </div>

      <el-form :model="scheduleForm" label-width="100px" class="schedule-form">
        <el-form-item label="Cron表达式">
          <el-input
            v-model="scheduleForm.cronExpression"
            placeholder="如：0 30 9 * * 1-5"
            clearable
            @input="handleCronInput"
          >
            <template #append>
              <el-button @click="handlePreviewCron" :loading="previewingCron">
                预览
              </el-button>
            </template>
          </el-input>
          <div class="cron-hint">
            格式：分 时 日 月 周（5位），例如：
            <el-tag
              v-for="example in cronExamples"
              :key="example.expr"
              size="small"
              effect="plain"
              class="cron-example-tag"
              @click="scheduleForm.cronExpression = example.expr; handlePreviewCron()"
            >
              {{ example.label }}
            </el-tag>
          </div>
        </el-form-item>

        <!-- 预览结果 -->
        <div v-if="cronPreview" class="cron-preview">
          <div class="preview-header">
            <el-icon><Clock /></el-icon>
            <span>含义：{{ cronPreview.description }}</span>
          </div>
          <div class="preview-times">
            <span class="preview-label">下次执行时间：</span>
            <div
              v-for="(time, index) in cronPreview.next_run_times"
              :key="index"
              class="preview-time-item"
            >
              <span class="time-index">{{ index + 1 }}</span>
              <span class="time-value">{{ time }}</span>
            </div>
          </div>
        </div>

        <!-- 预览错误 -->
        <div v-if="cronError" class="cron-error">
          <el-alert :title="cronError" type="error" :closable="false" show-icon />
        </div>
      </el-form>

      <template #footer>
        <div class="dialog-footer">
          <div v-if="currentSchedule" class="footer-left">
            <el-button type="danger" @click="handleDeleteSchedule" :loading="deletingSchedule">
              取消定时
            </el-button>
          </div>
          <div class="footer-right">
            <el-button @click="showScheduleDialog = false">关闭</el-button>
            <el-button
              type="primary"
              @click="handleScheduleSubmit"
              :disabled="!scheduleForm.cronExpression || !!cronError"
              :loading="submittingSchedule"
            >
              确认设置
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onUnmounted, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Cpu,
  VideoPlay,
  Clock,
  ArrowRight,
  Download,
  Refresh,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import { aiSelectorApi } from '@/api/aiSelector'
import { useAuthStore } from '@/stores/auth'

marked.setOptions({
  breaks: true,
  gfm: true
})

const authStore = useAuthStore()

// 分析师团队定义
const analystTeam = [
  {
    name: '大盘分析师',
    emoji: '📈',
    description: '分析大盘整体走势与市场环境',
    tags: ['指数分析', '北向资金', '涨跌比'],
    bgColor: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
  },
  {
    name: '主线板块分析师',
    emoji: '🔥',
    description: '识别当前市场主线热点板块',
    tags: ['涨停集中度', '5日强度', '资金流向'],
    bgColor: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)'
  },
  {
    name: '市场合力分析师',
    emoji: '💪',
    description: '分析主力与散户资金动向',
    tags: ['主力净流入', '散户净流入', '双向资金'],
    bgColor: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)'
  },
  {
    name: '股票龙头分析师',
    emoji: '👑',
    description: '筛选板块龙头与连板强势股',
    tags: ['连板分析', '板块排名', '成交量'],
    bgColor: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)'
  },
  {
    name: '风险分析师',
    emoji: '🛡️',
    description: '排除高风险标的，保障安全边际',
    tags: ['ST排除', '新股过滤', '退市风险'],
    bgColor: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)'
  },
  {
    name: '决策分析师',
    emoji: '🎯',
    description: '综合所有分析师结论，给出最终决策',
    tags: ['综合决策', '标的推荐', '风险评级'],
    bgColor: 'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)'
  }
]

// 运行状态
const running = ref(false)
const progress = ref(0)
const currentStep = ref('')
const elapsedTime = ref(0)
const currentTaskId = ref('')
let timer: ReturnType<typeof setInterval> | null = null
let pollingTimer: ReturnType<typeof setInterval> | null = null

// 结果数据
const hasResult = ref(false)
const resultTime = ref('')
const activeResultTab = ref('0')
const resultData = reactive<{
  analystResults: Array<{
    name: string
    conclusion: string
    tagType: 'success' | 'warning' | 'danger' | 'info'
    content: string
  }>
  decision: {
    action: string
    stocks: Array<{ code: string; name: string }>
    reasoning: string
    position_suggestion?: string
    risk_warning?: string
  } | null
  earlyStop: boolean
  earlyStopReason: string
}>({
  analystResults: [],
  decision: null,
  earlyStop: false,
  earlyStopReason: ''
})

// 定时运行
const showScheduleDialog = ref(false)
const scheduleForm = reactive({
  cronExpression: '',
})
const cronPreview = ref<{ cron_expression: string; description: string; next_run_times: string[] } | null>(null)
const cronError = ref('')
const previewingCron = ref(false)
const submittingSchedule = ref(false)
const deletingSchedule = ref(false)
const currentSchedule = ref<{ cron_expression: string; enabled: boolean; job_id: string; next_run_time: string | null } | null>(null)

// Cron 示例
const cronExamples = [
  { label: '每个工作日9:30', expr: '30 9 * * 1-5' },
  { label: '每天15:00', expr: '0 15 * * *' },
  { label: '每周一9:30', expr: '30 9 * * 1' },
  { label: '每月1日9:30', expr: '30 9 1 * *' },
]

// 进度条状态
const progressStatus = ref<'' | 'success' | 'exception' | 'warning'>('')

// 格式化时间
const formatTime = (seconds: number): string => {
  if (!seconds || seconds <= 0) return '0秒'
  if (seconds < 60) return `${Math.floor(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  return remainingSeconds > 0 ? `${minutes}分${remainingSeconds}秒` : `${minutes}分钟`
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
    '强烈推荐': 'success',
    '推荐': 'success',
    '谨慎推荐': 'warning',
    '观望': 'info',
    '规避': 'danger'
  }
  return map[action] || 'info'
}

// 立即运行
const handleRunNow = async () => {
  running.value = true
  progress.value = 0
  elapsedTime.value = 0
  currentStep.value = '正在提交AI选股任务...'
  progressStatus.value = ''

  // 计时器
  timer = setInterval(() => {
    elapsedTime.value += 1
  }, 1000)

  try {
    // 调用后端API启动任务
    const res = await aiSelectorApi.run()
    if (!res.success) {
      throw new Error(res.message || '启动任务失败')
    }

    currentTaskId.value = res.data.task_id
    currentStep.value = '任务已提交，等待处理...'
    progress.value = 5

    // 开始轮询任务状态
    startPolling()
  } catch (error: any) {
    running.value = false
    if (timer) { clearInterval(timer); timer = null }
    ElMessage.error(error.message || '启动AI选股任务失败')
  }
}

// 轮询任务状态
const startPolling = () => {
  if (pollingTimer) clearInterval(pollingTimer)

  pollingTimer = setInterval(async () => {
    if (!currentTaskId.value) return

    try {
      const res = await aiSelectorApi.getStatus(currentTaskId.value)
      if (!res.success || !res.data) return

      const task = res.data

      // 更新进度
      progress.value = task.progress || 0
      currentStep.value = task.current_step || ''

      if (task.status === 'completed') {
        // 任务完成，获取完整结果
        if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
        if (timer) { clearInterval(timer); timer = null }

        progress.value = 100
        progressStatus.value = 'success'
        currentStep.value = '分析完成'

        await fetchResult()
        running.value = false
        ElMessage.success('AI选股分析完成')

      } else if (task.status === 'failed') {
        // 任务失败
        if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null }
        if (timer) { clearInterval(timer); timer = null }

        progressStatus.value = 'exception'
        currentStep.value = '分析失败'
        running.value = false

        const errMsg = task.error_message || '分析过程中发生错误'
        ElMessage.error(errMsg)
      }
    } catch (error) {
      console.error('轮询AI选股任务状态失败:', error)
    }
  }, 3000)
}

// 获取完整结果
const fetchResult = async () => {
  if (!currentTaskId.value) return

  try {
    const res = await aiSelectorApi.getResult(currentTaskId.value)
    if (!res.success || !res.data) return

    const data = res.data
    hasResult.value = true
    resultTime.value = new Date().toLocaleString('zh-CN')
    activeResultTab.value = '0'

    // 填充分析师结果
    if (data.analyst_results && Array.isArray(data.analyst_results)) {
      resultData.analystResults = data.analyst_results.map((r: any) => ({
        name: r.name,
        conclusion: r.conclusion,
        tagType: r.conclusion === '已跳过' ? 'info' : (r.tag_type as 'success' | 'warning' | 'danger' | 'info'),
        content: r.content,
      }))
    }

    // 填充提前终止信息
    resultData.earlyStop = !!data.early_stop
    resultData.earlyStopReason = data.early_stop_reason || ''

    // 填充决策结果
    if (data.decision) {
      resultData.decision = data.decision
    }
  } catch (error) {
    console.error('获取AI选股结果失败:', error)
    ElMessage.error('获取结果失败')
  }
}

// 定时运行 - 打开对话框时加载当前配置
const handleOpenScheduleDialog = async () => {
  showScheduleDialog.value = true
  cronPreview.value = null
  cronError.value = ''
  await fetchCurrentSchedule()
  if (currentSchedule.value) {
    scheduleForm.cronExpression = currentSchedule.value.cron_expression
    handlePreviewCron()
  }
}

// 获取当前定时任务配置
const fetchCurrentSchedule = async () => {
  try {
    const res = await aiSelectorApi.getSchedule()
    if (res.success && res.data) {
      currentSchedule.value = res.data
    } else {
      currentSchedule.value = null
    }
  } catch {
    currentSchedule.value = null
  }
}

// 输入Cron表达式时的防抖预览
let cronDebounceTimer: ReturnType<typeof setTimeout> | null = null
const handleCronInput = () => {
  cronError.value = ''
  cronPreview.value = null
  if (cronDebounceTimer) clearTimeout(cronDebounceTimer)
  if (!scheduleForm.cronExpression.trim()) return
  cronDebounceTimer = setTimeout(() => {
    handlePreviewCron()
  }, 600)
}

// 预览Cron表达式
const handlePreviewCron = async () => {
  const expr = scheduleForm.cronExpression.trim()
  if (!expr) {
    cronPreview.value = null
    cronError.value = ''
    return
  }
  previewingCron.value = true
  cronError.value = ''
  try {
    const res = await aiSelectorApi.previewCron(expr)
    if (res.success && res.data) {
      cronPreview.value = res.data
    }
  } catch (error: any) {
    cronPreview.value = null
    const msg = error?.response?.data?.detail || error?.message || '无效的Cron表达式'
    cronError.value = msg
  } finally {
    previewingCron.value = false
  }
}

// 提交定时任务
const handleScheduleSubmit = async () => {
  const expr = scheduleForm.cronExpression.trim()
  if (!expr) {
    ElMessage.warning('请输入Cron表达式')
    return
  }
  submittingSchedule.value = true
  try {
    const res = await aiSelectorApi.createSchedule(expr)
    if (res.success) {
      ElMessage.success('定时任务设置成功')
      await fetchCurrentSchedule()
      showScheduleDialog.value = false
    }
  } catch (error: any) {
    const msg = error?.response?.data?.detail || error?.message || '设置定时任务失败'
    ElMessage.error(msg)
  } finally {
    submittingSchedule.value = false
  }
}

// 删除定时任务
const handleDeleteSchedule = async () => {
  deletingSchedule.value = true
  try {
    const res = await aiSelectorApi.deleteSchedule()
    if (res.success) {
      ElMessage.success('定时任务已取消')
      currentSchedule.value = null
      scheduleForm.cronExpression = ''
      cronPreview.value = null
    }
  } catch (error: any) {
    ElMessage.error('取消定时任务失败')
  } finally {
    deletingSchedule.value = false
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
  a.download = `AI选股结果_${new Date().toISOString().slice(0, 10)}.json`
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(url)
  document.body.removeChild(a)
  ElMessage.success('结果导出成功')
}

// 重新运行
const handleReset = () => {
  hasResult.value = false
  resultData.analystResults = []
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

onMounted(() => {
  fetchCurrentSchedule()
})
</script>

<style lang="scss" scoped>
.ai-selector {
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

  .selector-container {
    .team-card,
    .action-card,
    .results-card {
      border-radius: 16px;
      border: none;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
      margin-bottom: 24px;

      :deep(.el-card__header) {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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

    // 运行按钮
    .action-buttons {
      display: flex;
      justify-content: center;
      gap: 20px;

      .run-btn {
        width: 220px;
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

      .schedule-btn {
        width: 220px;
        height: 56px;
        font-size: 18px;
        font-weight: 700;
        border-radius: 16px;
        border: 2px solid #3b82f6;
        color: #3b82f6;
        transition: all 0.3s ease;

        &:hover {
          background: #eff6ff;
          transform: translateY(-3px);
          box-shadow: 0 8px 25px rgba(59, 130, 246, 0.15);
        }

        &.has-schedule {
          border-color: #10b981;
          color: #10b981;

          &:hover {
            background: #ecfdf5;
            box-shadow: 0 8px 25px rgba(16, 185, 129, 0.15);
          }
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

    // 结果区域
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

    .early-stop-section {
      margin-bottom: 20px;
    }

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

        .decision-stocks {
          margin-bottom: 16px;

          .stock-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;

            .stock-tag {
              font-size: 13px;
            }
          }
        }

        .decision-reasoning {
          p {
            margin: 8px 0 0;
            font-size: 14px;
            line-height: 1.8;
            color: #374151;
          }
        }

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

// 旋转动画
@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.rotating-icon {
  animation: rotate 2s linear infinite;
}

// 定时运行对话框样式
.current-schedule {
  margin-bottom: 20px;

  .schedule-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-top: 4px;
    font-size: 13px;

    code {
      background: #f0f9ff;
      padding: 1px 6px;
      border-radius: 4px;
      font-family: 'Courier New', monospace;
      color: #1e40af;
    }
  }
}

.schedule-form {
  .cron-hint {
    margin-top: 8px;
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.8;

    .cron-example-tag {
      cursor: pointer;
      margin-left: 4px;
      transition: all 0.2s;

      &:hover {
        color: #3b82f6;
        border-color: #3b82f6;
      }
    }
  }
}

.cron-preview {
  margin-top: 16px;
  padding: 16px;
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border-radius: 12px;
  border: 1px solid #bae6fd;

  .preview-header {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 600;
    color: #0369a1;
    margin-bottom: 12px;

    .el-icon {
      font-size: 18px;
    }
  }

  .preview-times {
    .preview-label {
      font-size: 12px;
      color: #64748b;
      margin-bottom: 8px;
      display: block;
    }

    .preview-time-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 6px 0;

      .time-index {
        width: 22px;
        height: 22px;
        border-radius: 50%;
        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 700;
        flex-shrink: 0;
      }

      .time-value {
        font-size: 13px;
        color: #1e40af;
        font-family: 'Courier New', monospace;
        font-weight: 500;
      }
    }
  }
}

.cron-error {
  margin-top: 12px;
}

.dialog-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;

  .footer-right {
    display: flex;
    gap: 8px;
  }
}
</style>
