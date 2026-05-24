<template>
  <div class="ai-selector-records">
    <!-- 页面标题 -->
    <div class="page-header">
      <h1 class="page-title">
        <el-icon><Document /></el-icon>
        AI选股-选股记录
      </h1>
      <p class="page-description">
        查看AI选股历史分析结果，追踪决策变化趋势
      </p>
    </div>

    <!-- 筛选和操作栏 -->
    <el-card class="filter-card" shadow="never">
      <el-row :gutter="16" align="middle">
        <el-col :span="6">
          <el-select v-model="statusFilter" placeholder="状态筛选" clearable @change="handleFilterChange">
            <el-option label="已完成" value="completed" />
            <el-option label="运行中" value="running" />
            <el-option label="失败" value="failed" />
          </el-select>
        </el-col>

        <el-col :span="6">
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

        <el-col :span="12">
          <div class="action-buttons">
            <el-button @click="refreshRecords">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- 记录列表 -->
    <el-card class="records-list-card" shadow="never">
      <el-table
        :data="filteredRecords"
        v-loading="loading"
        style="width: 100%"
      >
        <el-table-column prop="created_at" label="分析时间" width="180">
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

        <el-table-column label="决策" min-width="160">
          <template #default="{ row }">
            <template v-if="row.result?.decision">
              <el-tag :type="getActionTagType(row.result.decision.action)" size="small">
                {{ row.result.decision.action }}
              </el-tag>
            </template>
            <template v-else-if="row.status === 'completed'">
              <span class="text-gray">-</span>
            </template>
            <template v-else>
              <span class="text-gray">-</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="推荐标的" min-width="240">
          <template #default="{ row }">
            <template v-if="row.result?.decision?.stocks?.length">
              <div class="stock-tags">
                <el-tag
                  v-for="stock in row.result.decision.stocks.slice(0, 4)"
                  :key="stock.code"
                  size="small"
                  effect="plain"
                  class="stock-tag"
                >
                  {{ stock.name || stock.code }}
                </el-tag>
                <el-tag
                  v-if="row.result.decision.stocks.length > 4"
                  size="small"
                  type="info"
                >
                  +{{ row.result.decision.stocks.length - 4 }}
                </el-tag>
              </div>
            </template>
            <template v-else>
              <span class="text-gray">-</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ getStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="elapsed_time" label="耗时" width="100">
          <template #default="{ row }">
            <template v-if="row.elapsed_time || row.result?.elapsed_time">
              {{ (row.elapsed_time || row.result?.elapsed_time).toFixed(1) }}s
            </template>
            <template v-else>
              <span class="text-gray">-</span>
            </template>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" text size="small" @click="viewDetail(row)">
              查看详情
            </el-button>
            <el-button
              type="danger"
              text
              size="small"
              @click="deleteRecord(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
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
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Refresh } from '@element-plus/icons-vue'
import { aiSelectorApi, type AiSelectorHistoryItem } from '@/api/aiSelector'
import { formatDateTime } from '@/utils/datetime'

const router = useRouter()

const loading = ref(false)
const statusFilter = ref('')
const dateRange = ref<[string, string] | null>(null)
const currentPage = ref(1)
const pageSize = ref(20)
const totalRecords = ref(0)
const records = ref<AiSelectorHistoryItem[]>([])

const filteredRecords = computed(() => {
  let list = records.value
  if (statusFilter.value) {
    list = list.filter(r => r.status === statusFilter.value)
  }
  if (dateRange.value) {
    const [start, end] = dateRange.value
    list = list.filter(r => {
      const d = r.created_at?.substring(0, 10)
      return d && d >= start && d <= end
    })
  }
  return list
})

const fetchRecords = async () => {
  loading.value = true
  try {
    const res = await aiSelectorApi.getHistory({
      page: currentPage.value,
      page_size: pageSize.value,
    })
    if (res.success) {
      records.value = res.data.tasks
      totalRecords.value = res.data.total
    }
  } catch (e) {
    console.error('获取选股记录失败:', e)
    ElMessage.error('获取选股记录失败')
  } finally {
    loading.value = false
  }
}

const handleFilterChange = () => {
  currentPage.value = 1
  fetchRecords()
}

const refreshRecords = () => fetchRecords()

const viewDetail = (row: AiSelectorHistoryItem) => {
  router.push(`/ai-selector/records/${row.task_id}`)
}

const deleteRecord = async (row: AiSelectorHistoryItem) => {
  try {
    await ElMessageBox.confirm('确定要删除该选股记录吗？', '确认删除', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    const res = await aiSelectorApi.deleteHistory(row.task_id)
    if (res.success) {
      ElMessage.success('记录已删除')
      fetchRecords()
    }
  } catch (e: any) {
    if (e !== 'cancel') {
      console.error('删除记录失败:', e)
    }
  }
}

const getActionTagType = (action: string) => {
  if (action?.includes('强烈推荐')) return 'danger'
  if (action?.includes('谨慎推荐')) return 'warning'
  if (action?.includes('规避')) return 'info'
  if (action?.includes('观望')) return ''
  return 'success'
}

const getStatusType = (status: string) => {
  const map: Record<string, string> = { completed: 'success', running: 'warning', failed: 'danger', pending: 'info' }
  return map[status] || 'info'
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = { completed: '已完成', running: '运行中', failed: '失败', pending: '等待中' }
  return map[status] || status
}

const formatTime = (time: string) => formatDateTime(time)

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
.ai-selector-records {
  .page-header {
    margin-bottom: 24px;

    .page-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 24px;
      font-weight: 600;
      color: var(--el-text-color-primary);
      margin: 0 0 8px 0;
    }

    .page-description {
      color: var(--el-text-color-regular);
      margin: 0;
    }
  }

  .filter-card {
    margin-bottom: 24px;

    .action-buttons {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
    }
  }

  .records-list-card {
    .stock-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;

      .stock-tag {
        cursor: pointer;
      }
    }

    .text-gray {
      color: var(--el-text-color-placeholder);
    }

    .pagination-wrapper {
      display: flex;
      justify-content: center;
      margin-top: 24px;
    }
  }
}
</style>
