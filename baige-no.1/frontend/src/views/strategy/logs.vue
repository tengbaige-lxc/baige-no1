<template>
  <div class="page-container">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>策略日志</span>
          <el-button @click="$router.back()">返回</el-button>
        </div>
      </template>

      <el-table :data="logList" v-loading="loading" border stripe>
        <el-table-column type="index" width="50" />
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="signal" label="信号" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.signal === 'BUY'" type="danger">买入</el-tag>
            <el-tag v-else-if="row.signal === 'SELL'" type="success">卖出</el-tag>
            <el-tag v-else type="info">持有</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格" width="120">
          <template #default="{ row }">{{ row.price ? row.price.toFixed(2) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="120">
          <template #default="{ row }">{{ row.quantity || '-' }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="触发原因" min-width="200" />
        <el-table-column prop="created_at" label="时间" min-width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :page-sizes="[10, 20]"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getStrategyLogs } from '@/api/strategy'

const route = useRoute()
const loading = ref(false)
const logList = ref([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  strategy_id: route.query.strategy_id,
})

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function fetchLogs() {
  if (!queryParams.strategy_id) return
  loading.value = true
  try {
    const res = await getStrategyLogs(queryParams.strategy_id, queryParams)
    logList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function handlePageChange(val) {
  queryParams.page = val
  fetchLogs()
}

onMounted(fetchLogs)
</script>

<style scoped lang="scss">
.page-container {
  padding: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
