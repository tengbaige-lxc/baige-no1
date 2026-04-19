<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="search-form">
        <el-form :inline="true" :model="queryParams">
          <el-form-item label="交易对">
            <el-input v-model="queryParams.symbol" placeholder="BTCUSDT" clearable />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="queryParams.status" placeholder="全部" clearable style="width: 120px;">
              <el-option label="新建" value="NEW" />
              <el-option label="部分成交" value="PARTIALLY_FILLED" />
              <el-option label="完全成交" value="FILLED" />
              <el-option label="已撤销" value="CANCELED" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSearch">查询</el-button>
            <el-button @click="resetQuery">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table :data="orderList" v-loading="loading" border stripe>
        <el-table-column type="index" width="50" />
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="side" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'danger' : 'success'">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="order_type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.order_type === 'MARKET' ? '市价' : '限价' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量" width="120" />
        <el-table-column prop="price" label="价格" width="120">
          <template #default="{ row }">{{ row.price || '-' }}</template>
        </el-table-column>
        <el-table-column prop="executed_qty" label="成交数量" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="market_type" label="市场" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.market_type === 'SPOT' ? '现货' : '合约' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'NEW'" link type="danger" @click="handleCancel(row)">撤销</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getOrders, cancelOrder } from '@/api/trade'

const loading = ref(false)
const orderList = ref([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  symbol: '',
  status: '',
})

function statusType(status) {
  const map = { NEW: 'primary', PARTIALLY_FILLED: 'warning', FILLED: 'success', CANCELED: 'info', REJECTED: 'danger' }
  return map[status] || 'info'
}

function statusText(status) {
  const map = { NEW: '新建', PARTIALLY_FILLED: '部分成交', FILLED: '完全成交', CANCELED: '已撤销', REJECTED: '拒绝' }
  return map[status] || status
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function fetchOrders() {
  loading.value = true
  try {
    const res = await getOrders(queryParams)
    orderList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function handleSearch() { queryParams.page = 1; fetchOrders() }
function resetQuery() { queryParams.symbol = ''; queryParams.status = ''; queryParams.page = 1; fetchOrders() }
function handleSizeChange(val) { queryParams.page_size = val; fetchOrders() }
function handleCurrentChange(val) { queryParams.page = val; fetchOrders() }

function handleCancel(row) {
  ElMessageBox.confirm(`确定撤销 ${row.symbol} 的订单吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    await cancelOrder(row.id)
    ElMessage.success('撤销成功')
    fetchOrders()
  })
}

onMounted(fetchOrders)
</script>

<style scoped lang="scss">
.page-container {
  padding: 16px;
}
</style>
