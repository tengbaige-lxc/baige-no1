<template>
  <div class="reduce-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>📉 减仓管理</span>
          <el-button type="primary" @click="handleAdd">+ 新建减仓记录</el-button>
        </div>
      </template>

      <el-table :data="records" stripe v-loading="loading">
        <el-table-column prop="symbol" label="币种" width="130" />
        <el-table-column prop="direction" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.direction === 'LONG' ? 'success' : 'danger'" size="small">
              {{ row.direction }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="entry_price" label="开仓价" width="110" />
        <el-table-column prop="original_size" label="原始仓位" width="100" />
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
        <el-table-column prop="reduce_reason" label="减仓原因" min-width="150" show-overflow-tooltip />
        <el-table-column prop="updated_at" label="更新时间" width="160">
          <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleReduce(row)">减仓</el-button>
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

    <!-- 新建记录弹窗 -->
    <el-dialog v-model="addDialog" title="新建减仓记录" width="400px">
      <el-form :model="addForm" label-width="100px">
        <el-form-item label="币种">
          <el-input v-model="addForm.symbol" placeholder="BTC-USDT-SWAP" />
        </el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="addForm.direction">
            <el-radio-button label="LONG">做多</el-radio-button>
            <el-radio-button label="SHORT">做空</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="开仓价">
          <el-input-number v-model="addForm.entry_price" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="原始仓位">
          <el-input-number v-model="addForm.original_size" :precision="2" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialog = false">取消</el-button>
        <el-button type="primary" @click="submitAdd">确定</el-button>
      </template>
    </el-dialog>

    <!-- 减仓操作弹窗 -->
    <el-dialog v-model="reduceDialog" title="执行减仓" width="400px">
      <el-form :model="reduceForm" label-width="100px">
        <el-form-item label="减仓数量">
          <el-input-number v-model="reduceForm.reduced_size" :precision="2" :min="0.01" style="width: 100%" />
        </el-form-item>
        <el-form-item label="减仓原因">
          <el-select v-model="reduceForm.reduce_reason" placeholder="选择原因" style="width: 100%">
            <el-option label="背离信号减仓" value="背离信号减仓" />
            <el-option label="趋势线跌破减仓" value="趋势线跌破减仓" />
            <el-option label="清算密集区减仓" value="清算密集区减仓" />
            <el-option label="移动止损减仓" value="移动止损减仓" />
            <el-option label="时间止损减仓" value="时间止损减仓" />
            <el-option label="手动减仓" value="手动减仓" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reduceDialog = false">取消</el-button>
        <el-button type="primary" @click="submitReduce">确认减仓</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getReduceRecords, createReduceRecord, updateReduceRecord } from '@/api/reduceShort'

const loading = ref(false)
const records = ref([])
const addDialog = ref(false)
const reduceDialog = ref(false)
const currentId = ref(null)

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0
})

const addForm = reactive({
  symbol: '',
  direction: 'LONG',
  entry_price: 0,
  original_size: 0
})

const reduceForm = reactive({
  reduced_size: 0,
  reduce_reason: ''
})

async function fetchRecords() {
  loading.value = true
  try {
    const res = await getReduceRecords({ page: pagination.page, page_size: pagination.page_size })
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
  addForm.direction = 'LONG'
  addForm.entry_price = 0
  addForm.original_size = 0
  addDialog.value = true
}

async function submitAdd() {
  try {
    await createReduceRecord(addForm)
    ElMessage.success('创建成功')
    addDialog.value = false
    fetchRecords()
  } catch (e) {
    ElMessage.error('创建失败')
  }
}

function handleReduce(row) {
  currentId.value = row.id
  reduceForm.reduced_size = 0
  reduceForm.reduce_reason = ''
  reduceDialog.value = true
}

async function submitReduce() {
  try {
    await updateReduceRecord(currentId.value, reduceForm)
    ElMessage.success('减仓成功')
    reduceDialog.value = false
    fetchRecords()
  } catch (e) {
    ElMessage.error('减仓失败')
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
.reduce-page {
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
</style>
