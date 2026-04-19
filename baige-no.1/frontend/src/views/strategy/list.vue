<template>
  <div class="page-container">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>策略列表</span>
          <el-button type="primary" @click="handleAdd"><el-icon><Plus /></el-icon> 新建策略</el-button>
        </div>
      </template>

      <el-table :data="strategyList" v-loading="loading" border stripe>
        <el-table-column prop="name" label="策略名称" min-width="150" />
        <el-table-column prop="strategy_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small">{{ typeText(row.strategy_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="side" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'danger' : 'success'" size="small">{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-switch v-model="row.is_active" @change="(val) => toggleActive(row, val)" />
          </template>
        </el-table-column>
        <el-table-column prop="last_signal" label="最新信号" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.last_signal === 'BUY'" type="danger" size="small">买入</el-tag>
            <el-tag v-else-if="row.last_signal === 'SELL'" type="success" size="small">卖出</el-tag>
            <el-tag v-else type="info" size="small">持有</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_trades" label="交易次数" width="100" />
        <el-table-column prop="interval_seconds" label="轮询间隔" width="100">
          <template #default="{ row }">{{ row.interval_seconds }}s</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleViewLogs(row)">日志</el-button>
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
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

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="550px">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="策略名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="策略类型" prop="strategy_type">
          <el-select v-model="form.strategy_type" style="width: 100%;">
            <el-option label="网格交易" value="grid" />
            <el-option label="均线交叉" value="ma_cross" />
            <el-option label="RSI 指标" value="rsi" />
            <el-option label="自定义" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="交易对" prop="symbol">
          <el-input v-model="form.symbol" placeholder="BTCUSDT" />
        </el-form-item>
        <el-form-item label="市场">
          <el-radio-group v-model="form.market_type">
            <el-radio-button label="SPOT">现货</el-radio-button>
            <el-radio-button label="FUTURES">合约</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="策略方向">
          <el-radio-group v-model="form.side">
            <el-radio-button label="BUY">买入</el-radio-button>
            <el-radio-button label="SELL">卖出</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="参数 (JSON)">
          <el-input v-model="paramsJson" type="textarea" rows="3" placeholder='{"quantity": 0.001, "grid_low": 60000, "grid_high": 70000}' />
        </el-form-item>
        <el-form-item label="轮询间隔">
          <el-input-number v-model="form.interval_seconds" :min="10" :max="3600" /> 秒
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitLoading">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getStrategies, createStrategy, updateStrategy, deleteStrategy } from '@/api/strategy'

const router = useRouter()
const loading = ref(false)
const submitLoading = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEdit = ref(false)
const formRef = ref(null)
const strategyList = ref([])
const total = ref(0)

const queryParams = reactive({ page: 1, page_size: 10 })

const form = reactive({
  id: null,
  name: '',
  strategy_type: 'grid',
  symbol: 'BTCUSDT',
  market_type: 'SPOT',
  side: 'BUY',
  params: {},
  interval_seconds: 60,
  remark: '',
})

const paramsJson = computed({
  get: () => JSON.stringify(form.params, null, 2),
  set: (val) => { try { form.params = JSON.parse(val) } catch {} },
})

const formRules = {
  name: [{ required: true, message: '请输入策略名称', trigger: 'blur' }],
  strategy_type: [{ required: true, message: '请选择策略类型', trigger: 'change' }],
  symbol: [{ required: true, message: '请输入交易对', trigger: 'blur' }],
}

function typeText(type) {
  const map = { grid: '网格', ma_cross: '均线交叉', rsi: 'RSI', custom: '自定义' }
  return map[type] || type
}

async function fetchStrategies() {
  loading.value = true
  try {
    const res = await getStrategies(queryParams)
    strategyList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.id = null
  form.name = ''
  form.strategy_type = 'grid'
  form.symbol = 'BTCUSDT'
  form.market_type = 'SPOT'
  form.side = 'BUY'
  form.params = {}
  form.interval_seconds = 60
  form.remark = ''
}

function handleAdd() {
  resetForm()
  isEdit.value = false
  dialogTitle.value = '新建策略'
  dialogVisible.value = true
}

function handleEdit(row) {
  isEdit.value = true
  dialogTitle.value = '编辑策略'
  Object.assign(form, row)
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitLoading.value = true
  try {
    const data = { ...form }
    delete data.id
    if (isEdit.value) {
      await updateStrategy(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createStrategy(data)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchStrategies()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

async function toggleActive(row, val) {
  try {
    await updateStrategy(row.id, { is_active: val })
    ElMessage.success(val ? '策略已启用' : '策略已暂停')
  } catch {
    row.is_active = !val
    ElMessage.error('操作失败')
  }
}

function handleViewLogs(row) {
  router.push(`/strategy/logs?strategy_id=${row.id}`)
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除策略 "${row.name}" 吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    await deleteStrategy(row.id)
    ElMessage.success('删除成功')
    fetchStrategies()
  })
}

function handlePageChange(val) {
  queryParams.page = val
  fetchStrategies()
}

onMounted(fetchStrategies)
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
