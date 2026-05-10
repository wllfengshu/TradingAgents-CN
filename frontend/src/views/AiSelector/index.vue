<template>
  <div class="ai-selector">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="title-section">
          <h1 class="page-title">
            <el-icon class="title-icon"><Cpu /></el-icon>
            AI选股
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
          <h4 class="section-title">协同流程</h4>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="step-number">1</div>
              <div class="step-text">多维分析</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">2</div>
              <div class="step-text">交叉验证</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">3</div>
              <div class="step-text">风险过滤</div>
            </div>
            <div class="flow-arrow">
              <el-icon><ArrowRight /></el-icon>
            </div>
            <div class="flow-step">
              <div class="step-number">4</div>
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
            @click="showScheduleDialog = true"
          >
            <el-icon><Clock /></el-icon>
            定时运行
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
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form :model="scheduleForm" label-width="100px">
        <el-form-item label="运行频率">
          <el-select v-model="scheduleForm.frequency" style="width: 100%">
            <el-option label="每天" value="daily" />
            <el-option label="每周" value="weekly" />
            <el-option label="每月" value="monthly" />
          </el-select>
        </el-form-item>
        <el-form-item label="运行时间">
          <el-time-picker
            v-model="scheduleForm.time"
            placeholder="选择运行时间"
            format="HH:mm"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item v-if="scheduleForm.frequency === 'weekly'" label="运行日">
          <el-select v-model="scheduleForm.weekday" style="width: 100%">
            <el-option label="周一" :value="1" />
            <el-option label="周二" :value="2" />
            <el-option label="周三" :value="3" />
            <el-option label="周四" :value="4" />
            <el-option label="周五" :value="5" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="scheduleForm.frequency === 'monthly'" label="运行日期">
          <el-input-number
            v-model="scheduleForm.monthDay"
            :min="1"
            :max="28"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showScheduleDialog = false">取消</el-button>
        <el-button type="primary" @click="handleScheduleSubmit">确认设置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onUnmounted } from 'vue'
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

marked.setOptions({
  breaks: true,
  gfm: true
})

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
let timer: ReturnType<typeof setInterval> | null = null
let progressTimer: ReturnType<typeof setInterval> | null = null

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
  } | null
}>({
  analystResults: [],
  decision: null
})

// 定时运行
const showScheduleDialog = ref(false)
const scheduleForm = reactive({
  frequency: 'daily',
  time: new Date(2026, 0, 1, 9, 30),
  weekday: 1,
  monthDay: 1
})

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
  currentStep.value = '正在初始化AI选股分析...'
  progressStatus.value = ''

  // 模拟计时
  timer = setInterval(() => {
    elapsedTime.value += 1
  }, 1000)

  // 模拟进度
  const steps = [
    { name: '大盘分析师工作中...', target: 15 },
    { name: '主线板块分析师工作中...', target: 30 },
    { name: '市场合力分析师工作中...', target: 50 },
    { name: '股票龙头分析师工作中...', target: 65 },
    { name: '风险分析师工作中...', target: 80 },
    { name: '决策分析师综合研判...', target: 100 }
  ]

  let stepIndex = 0
  progressTimer = setInterval(() => {
    if (stepIndex < steps.length) {
      const step = steps[stepIndex]
      currentStep.value = step.name
      progress.value = Math.min(progress.value + Math.random() * 5 + 2, step.target)

      if (progress.value >= step.target) {
        stepIndex++
      }
    }

    if (progress.value >= 100) {
      progress.value = 100
      if (progressTimer) clearInterval(progressTimer)
      if (timer) clearInterval(timer)
      progressStatus.value = 'success'
      currentStep.value = '分析完成'

      // 模拟结果
      setTimeout(() => {
        running.value = false
        hasResult.value = true
        resultTime.value = new Date().toLocaleString('zh-CN')
        activeResultTab.value = '0'
        resultData.analystResults = [
          {
            name: '大盘分析师',
            conclusion: '偏多',
            tagType: 'success',
            content: '## 大盘分析结论\n\n当前上证指数处于中期上升趋势，MACD金叉形成，北向资金连续3日净流入，涨跌比2.5:1，市场整体偏强。\n\n**关键指标：**\n- 上证指数：3,350点，涨幅+0.85%\n- 北向资金：净流入+52亿\n- 涨跌比：2.5:1\n- 成交额：1.2万亿'
          },
          {
            name: '主线板块分析师',
            conclusion: '科技+新能源',
            tagType: 'success',
            content: '## 板块分析结论\n\n当前市场主线聚焦于人工智能和新能源板块，涨停集中度最高。\n\n**热门板块：**\n- AI算力：涨停集中度38%，5日强度+12%\n- 半导体：涨停集中度25%，5日强度+8%\n- 新能源：涨停集中度20%，5日强度+6%\n- 医药：涨停集中度8%，5日强度+2%'
          },
          {
            name: '市场合力分析师',
            conclusion: '主力流入',
            tagType: 'success',
            content: '## 合力分析结论\n\n主力资金持续流入科技板块，散户资金跟随，形成正向合力。\n\n**资金动向：**\n- 主力净流入：+38亿（科技）、+22亿（新能源）\n- 散户净流入：+15亿（科技）、+8亿（新能源）\n- 合力方向：正向共振'
          },
          {
            name: '股票龙头分析师',
            conclusion: '多只强势',
            tagType: 'success',
            content: '## 龙头分析结论\n\nAI算力板块龙头连板强势，半导体板块出现补涨龙头。\n\n**强势标的：**\n- 中际旭创：3连板，AI算力龙头\n- 寒武纪：2连板，AI芯片\n- 宁德时代：1板+大阳，新能源龙头'
          },
          {
            name: '风险分析师',
            conclusion: '风险可控',
            tagType: 'warning',
            content: '## 风险分析结论\n\n已排除ST、*ST及退市风险股，注意新股炒作风险。\n\n**风险提示：**\n- 已排除ST/*ST股：15只\n- 已排除退市风险股：3只\n- 新股炒作风险：2只需警惕\n- 整体风险评级：中等偏低'
          }
        ]
        resultData.decision = {
          action: '谨慎推荐',
          stocks: [
            { code: '300308', name: '中际旭创' },
            { code: '688256', name: '寒武纪' },
            { code: '300750', name: '宁德时代' }
          ],
          reasoning: '大盘偏多、主线明确（AI+新能源）、主力资金正流入、龙头股强势，但需注意短期涨幅较大带来的回调风险。建议分批建仓，控制仓位在30%以内。'
        }

        ElMessage.success('AI选股分析完成')
      }, 500)
    }
  }, 800)
}

// 定时运行
const handleScheduleSubmit = () => {
  const freqMap: Record<string, string> = {
    daily: '每天',
    weekly: '每周',
    monthly: '每月'
  }
  const freq = freqMap[scheduleForm.frequency] || scheduleForm.frequency
  const timeStr = scheduleForm.time
    ? new Date(scheduleForm.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : '09:30'

  ElMessage.success(`已设置${freq} ${timeStr} 定时运行AI选股`)
  showScheduleDialog.value = false
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
  progress.value = 0
  elapsedTime.value = 0
  currentStep.value = ''
  progressStatus.value = ''
}

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (progressTimer) clearInterval(progressTimer)
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
        align-items: center;
        justify-content: center;
        gap: 16px;

        .flow-step {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;

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
        }

        .flow-arrow {
          color: #c0c4cc;
          font-size: 20px;
          margin-top: -20px;
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
</style>
