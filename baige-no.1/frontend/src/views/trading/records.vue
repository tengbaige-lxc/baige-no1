<template>
  <div class="records-page">
    <el-card class="filter-card">
      <div class="filter-row">
        <el-input
          v-model="filter.symbol"
          placeholder="币种筛选"
          clearable
          style="width: 160px"
          @change="handleFilter"
        />
        <el-select
          v-model="filter.is_closed"
          placeholder="状态"
          clearable
          style="width: 120px"
          @change="handleFilter"
        >
          <el-option label="持仓中" :value="false" />
          <el-option label="已平仓" :value="true" />
        </el-select>
        <el-button type="primary" @click="handleAdd">+ 新增记录</el-button>
      </div>
    </el-card>

    <el-card class="table-card">
      <el-table :data="records" stripe style="width: 100%" v-loading="loading">
        <el-table-column prop="symbol" label="币种" width="140" />
        <el-table-column prop="direction" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.direction === 'LONG' ? 'success' : 'danger'" size="small">
              {{ row.direction }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="entry_price" label="开仓价" width="120">
          <template #default="{ row }">{{ formatPrice(row.entry_price) }}</template>
        </el-table-column>
        <el-table-column prop="exit_price" label="平仓价" width="120">
          <template #default="{ row }">{{ row.exit_price ? formatPrice(row.exit_price) : '-' }}</template>
        </el-table-column>
        <el-table-column prop="position_size" label="持仓量" width="100" />
        <el-table-column prop="pnl_usdt" label="盈亏(USDT)" width="130">
          <template #default="{ row }">
            <span :class="getPnlClass(row.pnl_usdt)">
              {{ row.pnl_usdt !== null ? formatPnl(row.pnl_usdt) : '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="leverage" label="杠杆" width="70">
          <template #default="{ row }">{{ row.leverage }}x</template>
        </el-table-column>
        <el-table-column prop="strategy_tag" label="策略标签" width="120">
          <template #default="{ row }">{{ row.strategy_tag || '-' }}</template>
        </el-table-column>
        <el-table-column prop="is_closed" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_closed ? 'info' : 'warning'" size="small">
              {{ row.is_closed ? '已平仓' : '持仓中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="pagination.page"
        v-model:page-size="pagination.page_size"
        :total="pagination.total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @size-change="handleSizeChange"
        @current-change="handlePageChange"
        class="pagination"
      />
    </el-card>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑交易记录' : '新增交易记录'" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="币种">
          <el-input v-model="form.symbol" placeholder="如 BTC-USDT-SWAP" />
        </el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="form.direction">
            <el-radio-button label="LONG">做多</el-radio-button>
            <el-radio-button label="SHORT">做空</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="开仓价">
          <el-input-number v-model="form.entry_price" :precision="4" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="平仓价">
          <el-input-number v-model="form.exit_price" :precision="4" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="持仓量">
          <el-input-number v-model="form.position_size" :precision="2" :min="0" style="width: 100%" />
        </el-form-item>
        <el-form-item label="盈亏(USDT)">
          <el-input-number v-model="form.pnl_usdt" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="杠杆">
          <el-input-number v-model="form.leverage" :min="1" :max="125" style="width: 100%" />
        </el-form-item>
        <el-form-item label="策略标签">
          <el-input v-model="form.strategy_tag" placeholder="如 网格策略" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.is_closed" active-text="已平仓" inactive-text="持仓中" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.notes" type="textarea" rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getTradeRecords, createTradeRecord, updateTradeRecord, deleteTradeRecord } from '@/api/tradeRecord'

const loading = ref(false)
const records = ref([])
const dialogVisible = ref(false)
const isEdit = ref(false)
const currentId = ref(null)

const filter = reactive({
  symbol: '',
  is_closed: null
})

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0
})

const form = reactive({
  symbol: '',
  direction: 'LONG',
  entry_price: 0,
  exit_price: null,
  position_size: 0,
  pnl_usdt: null,
  leverage: 20,
  strategy_tag: '',
  notes: '',
  is_closed: false
})

async function fetchRecords() {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      ...(filter.symbol && { symbol: filter.symbol }),
      ...(filter.is_closed !== null && { is_closed: filter.is_closed })
    }
    const res = await getTradeRecords(params)
    if (res.data.code === 200) {
      records.value = res.data.data.items
      pagination.total = res.data.data.total
    }
  } catch (e) {
    ElMessage.error('获取交易记录失败')
  } finally {
    loading.value = false
  }
}

function handleFilter() {
  pagination.page = 1
  fetchRecords()
}

function handleSizeChange(val) {
  pagination.page_size = val
  fetchRecords()
}

function handlePageChange(val) {
  pagination.page = val
  fetchRecords()
}

function resetForm() {
  form.symbol = ''
  form.direction = 'LONG'
  form.entry_price = 0
  form.exit_price = null
  form.position_size = 0
  form.pnl_usdt = null
  form.leverage = 20
  form.strategy_tag = ''
  form.notes = ''
  form.is_closed = false
}

function handleAdd() {
  isEdit.value = false
  currentId.value = null
  resetForm()
  dialogVisible.value = true
}

function handleEdit(row) {
  isEdit.value = true
  currentId.value = row.id
  Object.assign(form, row)
  dialogVisible.value = true
}

async function handleSubmit() {
  try {
    if (isEdit.value) {
      await updateTradeRecord(currentId.value, form)
      ElMessage.success('更新成功')
    } else {
      await createTradeRecord(form)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchRecords()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm('确定删除该交易记录？', '提示', { type: 'warning' })
    await deleteTradeRecord(row.id)
    ElMessage.success('删除成功')
    fetchRecords()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

function formatPrice(val) {
  return val ? val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '-'
}

function formatPnl(val) {
  const prefix = val >= 0 ? '+' : ''
  return prefix + val.toFixed(2)
}

function getPnlClass(val) {
  if (val === null) return ''
  return val >= 0 ? 'pnl-positive' : 'pnl-negative'
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN')
}

onMounted(fetchRecords)
</script>

<style scoped lang="scss">
.records-page {
  padding: 16px;
}
.filter-card {
  margin-bottom: 16px;
  .filter-row {
    display: flex;
    gap: 12px;
    align-items: center;
  }
}
.table-card {
  .pagination {
    margin-top: 16px;
    justify-content: flex-end;
  }
}
.pnl-positive {
  color: #3fb950;
  font-weight: 600;
}
.pnl-negative {
  color: #f85149;
  font-weight: 600;
}
</style>
