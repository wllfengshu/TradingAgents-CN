<template>
  <div class="ai-selector-record-detail">
    <!-- 顶部导航 -->
    <div class="detail-header">
      <el-button @click="goBack" text>
        <el-icon><ArrowLeft /></el-icon>
        返回选股记录
      </el-button>
      <div class="header-info" v-if="record">
        <el-tag :type="getActionTagType(record.decision?.action)" size="large" v-if="record.decision?.action">
          {{ record.decision.action }}
        </el-tag>
        <span class="header-time" v-if="record?.completed_at">
          分析时间：{{ formatTime(record.completed_at) }}
        </span>
        <span class="header-elapsed" v-if="record?.elapsed_time">
          耗时：{{ record.elapsed_time.toFixed(1) }}s
        </span>
      </div>
    </div>

    <div v-loading="loading" class="detail-content">
      <template v-if="record">
        <!-- 提前终止提示 -->
        <el-alert
          v-if="record.early_stop"
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 16px;"
        >
          <template #title>
            <span style="font-weight: bold;">分析提前终止：{{ record.early_stop_reason }}</span>
          </template>
        </el-alert>

        <!-- 决策卡片 -->
        <el-card v-if="record.decision" class="decision-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span class="card-title">综合决策</span>
              <el-tag :type="getActionTagType(record.decision.action)">
                {{ record.decision.action }}
              </el-tag>
            </div>
          </template>

          <!-- 推荐标的 -->
          <div class="stocks-section" v-if="record.decision.stocks?.length">
            <h4>推荐标的</h4>
            <div class="stock-cards">
              <el-card
                v-for="stock in record.decision.stocks"
                :key="stock.code"
                class="stock-card"
                shadow="hover"
              >
                <div class="stock-info">
                  <span class="stock-name">{{ stock.name || stock.code }}</span>
                  <span class="stock-code">{{ stock.code }}</span>
                </div>
                <div class="stock-reason" v-if="stock.reason">
                  {{ stock.reason }}
                </div>
              </el-card>
            </div>
          </div>

          <!-- 决策依据 -->
          <div class="reasoning-section" v-if="record.decision.reasoning">
            <h4>决策依据</h4>
            <p class="reasoning-text">{{ record.decision.reasoning }}</p>
          </div>

          <!-- 仓位建议 -->
          <div class="position-section" v-if="record.decision.position_suggestion">
            <h4>仓位建议</h4>
            <p>{{ record.decision.position_suggestion }}</p>
          </div>

          <!-- 风险提示 -->
          <div class="risk-section" v-if="record.decision.risk_warning">
            <h4>风险提示</h4>
            <el-alert :title="record.decision.risk_warning" type="warning" :closable="false" show-icon />
          </div>
        </el-card>

        <!-- 各分析师报告 -->
        <el-card class="analysts-card" shadow="never">
          <template #header>
            <span class="card-title">分析师报告</span>
          </template>

          <el-tabs v-if="record.analyst_results?.length">
            <el-tab-pane
              v-for="(analyst, idx) in record.analyst_results"
              :key="idx"
              :label="analyst.name"
            >
              <div class="analyst-header">
                <el-tag :type="analyst.tag_type as any" size="small">
                  {{ analyst.conclusion }}
                </el-tag>
              </div>
              <div class="analyst-content" v-html="renderMarkdown(analyst.content)"></div>
            </el-tab-pane>
          </el-tabs>

          <el-empty v-else description="暂无分析师报告" />
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
import { aiSelectorApi, type AiSelectorResult } from '@/api/aiSelector'
import { formatDateTime } from '@/utils/datetime'
import { marked } from 'marked'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const record = ref<AiSelectorResult | null>(null)

const fetchDetail = async () => {
  const taskId = route.params.id as string
  if (!taskId) return

  loading.value = true
  try {
    const res = await aiSelectorApi.getHistoryDetail(taskId)
    if (res.success && res.data) {
      record.value = res.data
    }
  } catch (e) {
    console.error('获取记录详情失败:', e)
    ElMessage.error('获取记录详情失败')
  } finally {
    loading.value = false
  }
}

const goBack = () => router.push('/ai-selector/records')

const getActionTagType = (action: string) => {
  if (action?.includes('强烈推荐')) return 'danger'
  if (action?.includes('谨慎推荐')) return 'warning'
  if (action?.includes('规避')) return 'info'
  if (action?.includes('观望')) return ''
  return 'success'
}

const formatTime = (time: string) => formatDateTime(time)

const renderMarkdown = (content: string) => {
  if (!content) return ''
  return marked(content)
}

onMounted(() => fetchDetail())
</script>

<style lang="scss" scoped>
.ai-selector-record-detail {
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

    .stock-cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;

      .stock-card {
        .stock-info {
          display: flex;
          align-items: baseline;
          gap: 8px;
          margin-bottom: 6px;

          .stock-name {
            font-size: 16px;
            font-weight: 600;
          }

          .stock-code {
            font-size: 13px;
            color: var(--el-text-color-secondary);
          }
        }

        .stock-reason {
          font-size: 13px;
          color: var(--el-text-color-regular);
          line-height: 1.5;
        }
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
