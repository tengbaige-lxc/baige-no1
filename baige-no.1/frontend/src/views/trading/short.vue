<template>
  <div class="short-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>📉 做空管理</span>
          <el-button type="danger" @click="handleAdd">+ 新建做空</el-button>
        </div>
      </template>

      <el-table :data="records" stripe v-loading="loading">
        <el-table-column prop="symbol" label="币种" width="130" />
        <el-table-column prop="entry_price" label="开仓价" width="110" />
        <el-table-column prop="original_size" label="开仓量" width="100" />
        <el-table-column prop="reduced_count" label="减仓次数" width="90" />
        <el-table-column prop="reduced_size" label="已减仓量" width="100" />
        <el-table-column prop="remaining_size" label="剩余仓位" width="100">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round((row.remaining_size / row.original_size) * 100)"
              :color="row.remaining_size / row.original_size > 0.5 ? '#58a6ff' : '#f85149'"
              :stroke-width="10"
            />
          </template>
        </el-table-column>
        <el-table-column prop="close_price" label="平仓价" width="100">
          <template #default="{ row }">{{ row.close_price || '-' }}</template>
        </el-table-column>
        <el-table-column prop="pnl_usdt" label="盈亏" width="120">
          <template #default="{ row }">
            <span :class="row.pnl_usdt >= 0 ? 'positive' : 'negative'" v-if="row.pnl_usdt !== null">
              {{ row.pnl_usdt >= 0 ? '+' : '' }}{{ row.pnl_usdt.toFixed(2) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'OPEN' ? 'warning' : 'info'" size="small">
              {{ row.status === 'OPEN' ? '持仓中' : '已平仓' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="close_reason" label="平仓原因" min-width="150" show-overflow-tooltip />
        <el-table-column prop="created_at" label="开仓时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'OPEN'" link type="danger" size="small" @click="handleClose(row)">平仓</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="pagination.page"
        v-model:page-size="pagination.page_size"
        :total="pagination.total"
        layout="total, prev, pager, next"
        @current-change="handlePageChange"
        class="pagination"
      />
    </el-card>

    <!-- 新建做空弹窗 -->
    <el-dialog v-model="addDialog" title="新建做空记录" width="400px">
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="币种">
          <el-input v-model="addForm.symbol" placeholder="BTC-USDT-SWAP" />
        </el-form-item>
        <el-form-item label="开仓价">
          <el-input-number v-model="addForm.entry_price" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="开仓量">
          <el-input-number v-model="addForm.original_size" :precision="2" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="danger" @click="submitAdd">确定做空</el-button>
      </template>
    </el-dialog>

    <!-- 平仓弹窗 -->
    <el-dialog v-model="closeDialog" title="平仓操作" width="400px">
      <el-form :model="closeForm" label-width="100px">
        <el-form-item label="平仓价">
          <el-input-number v-model="closeForm.close_price" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="盈亏(USDT)">
          <el-input-number v-model="closeForm.pnl_usdt" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="平仓原因">
          <el-select v-model="closeForm.close_reason" placeholder="选择原因" style="width: 100%">
            <el-option label="底背离平仓" value="底背离平仓" />
            <el-option label="趋势突破平仓" value="趋势突破平仓" />
            <el-option label="移动止损平仓" value="移动止损平仓" />
            <el-option label="目标止盈平仓" value="目标止盈平仓" />
            <el-option label="手动平仓" value="手动平仓" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="closeDialog = false">取消</el-button>
        <el-button type="danger" @click="submitClose">确认平仓</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getShortRecords, createShortRecord, updateShortRecord } from '@/api/reduceShort'

const loading = ref(false)
const records = ref([])
const addDialog = ref(false)
const closeDialog = ref(false)
const currentId = ref(null)

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0
})

const addForm = reactive({
  symbol: '',
  entry_price: 0,
  original_size: 0
})

const closeForm = reactive({
  close_price: 0,
  pnl_usdt: 0,
  close_reason: ''
})

async function fetchRecords() {
  loading.value = true
  try {
    const res = await getShortRecords({ page: pagination.page, page_size: pagination.page_size })
    if (res.data.code === 200) {
      records.value = res.data.data.items
      pagination.total = res.data.data.total
    }
  } catch (e) {
    ElMessage.error('获取记录失败')
  } finally {
    loading.value = false
  }
}

function handleAdd() {
  addForm.symbol = ''
  addForm.entry_price = 0
  addForm.original_size = 0
  addDialog.value = true
}

async function submitAdd() {
  try {
    await createShortRecord(addForm)
    ElMessage.success('创建成功')
    addDialog.value = false
    fetchRecords()
  } catch (e) {
    ElMessage.error('创建失败')
  }
}

function handleClose(row) {
  currentId.value = row.id
  closeForm.close_price = 0
  closeForm.pnl_usdt = 0
  closeForm.close_reason = ''
  closeDialog.value = true
}

async function submitClose() {
  try {
    await updateShortRecord(currentId.value, closeForm)
    ElMessage.success('平仓成功')
    closeDialog.value = false
    fetchRecords()
  } catch (e) {
    ElMessage.error('平仓失败')
  }
}

function handlePageChange(val) {
  pagination.page = val
  fetchRecords()
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

onMounted(fetchRecords)
</script>

<style scoped lang="scss">
.short-page {
  padding: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.pagination {
  margin-top: 12px;
  justify-content: flex-end;
}
.positive { color: #3fb950; font-weight: 600; }
.negative { color: #f85149; font-weight: 600; }
</style>
