<template>
  <div class="risk-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>风控配置管理</span>
          <el-button type="primary" @click="handleAdd">+ 新建配置</el-button>
        </div>
      </template>

      <el-table :data="configs" stripe v-loading="loading">
        <el-table-column prop="name" label="配置名称" width="150" />
        <el-table-column prop="leverage" label="杠杆" width="80">
          <template #default="{ row }">{{ row.leverage }}x</template>
        </el-table-column>
        <el-table-column prop="position_percent" label="单仓比例" width="100">
          <template #default="{ row }">{{ (row.position_percent * 100).toFixed(0) }}%</template>
        </el-table-column>
        <el-table-column prop="max_positions" label="最大持仓" width="90" />
        <el-table-column prop="activation_percent" label="移动止损激活" width="120">
          <template #default="{ row }">+{{ (row.activation_percent * 100).toFixed(0) }}%</template>
        </el-table-column>
        <el-table-column prop="callback_ratio" label="回撤触发" width="100">
          <template #default="{ row }">{{ (row.callback_ratio * 100).toFixed(0) }}%</template>
        </el-table-column>
        <el-table-column prop="max_hold_hours" label="时间止损" width="100">
          <template #default="{ row }">{{ row.max_hold_hours }}h</template>
        </el-table-column>
        <el-table-column prop="symbols" label="监控币种" min-width="200">
          <template #default="{ row }">
            <el-tag v-for="sym in row.symbols.slice(0, 3)" :key="sym" size="small" class="sym-tag">
              {{ sym.split('-')[0] }}
            </el-tag>
            <span v-if="row.symbols.length > 3" class="more">+{{ row.symbols.length - 3 }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 配置弹窗 -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑风控配置' : '新建风控配置'" width="680px">
      <el-form :model="form" label-width="140px" class="risk-form">
        <el-divider content-position="left">基础参数</el-divider>
        <el-form-item label="配置名称">
          <el-input v-model="form.name" placeholder="如：激进型/保守型" />
        </el-form-item>
        <el-form-item label="杠杆倍数">
          <el-slider v-model="form.leverage" :min="1" :max="125" show-input />
        </el-form-item>
        <el-form-item label="单币种仓位比例">
          <el-slider v-model="form.position_percent" :min="0.05" :max="1" :step="0.05" show-input :format-tooltip="v => (v*100).toFixed(0)+'%'" />
        </el-form-item>
        <el-form-item label="最大持仓数">
          <el-input-number v-model="form.max_positions" :min="1" :max="20" />
        </el-form-item>
        <el-form-item label="单日最大亏损">
          <el-slider v-model="form.max_daily_loss_percent" :min="0.01" :max="0.2" :step="0.01" show-input :format-tooltip="v => (v*100).toFixed(0)+'%'" />
        </el-form-item>

        <el-divider content-position="left">移动止损</el-divider>
        <el-form-item label="激活涨幅">
          <el-slider v-model="form.activation_percent" :min="0.01" :max="0.2" :step="0.01" show-input :format-tooltip="v => '+'+(v*100).toFixed(0)+'%'" />
        </el-form-item>
        <el-form-item label="回撤触发比例">
          <el-slider v-model="form.callback_ratio" :min="0.1" :max="0.8" :step="0.05" show-input :format-tooltip="v => (v*100).toFixed(0)+'%'" />
        </el-form-item>

        <el-divider content-position="left">时间止损</el-divider>
        <el-form-item label="启用时间止损">
          <el-switch v-model="form.time_stop_enabled" />
        </el-form-item>
        <el-form-item label="最大持仓时间">
          <el-input-number v-model="form.max_hold_hours" :min="1" :max="168" />
          <span class="unit">小时</span>
        </el-form-item>
        <el-form-item label="超期减仓比例">
          <el-slider v-model="form.time_stop_reduce_ratio" :min="0.1" :max="1" :step="0.1" show-input :format-tooltip="v => (v*100).toFixed(0)+'%'" />
        </el-form-item>

        <el-divider content-position="left">ATR止损</el-divider>
        <el-form-item label="启用ATR止损">
          <el-switch v-model="form.atr_stop_enabled" />
        </el-form-item>
        <el-form-item label="ATR周期">
          <el-input-number v-model="form.atr_period" :min="5" :max="50" />
        </el-form-item>
        <el-form-item label="ATR倍数">
          <el-input-number v-model="form.atr_multiplier" :min="0.5" :max="5" :precision="1" />
        </el-form-item>

        <el-divider content-position="left">风控限制</el-divider>
        <el-form-item label="单日最大交易">
          <el-input-number v-model="form.max_trades_per_day" :min="1" :max="50" />
        </el-form-item>
        <el-form-item label="亏损冷却时间">
          <el-input-number v-model="form.cooldown_after_loss" :min="0" :max="24" />
          <span class="unit">小时</span>
        </el-form-item>
        <el-form-item label="相关性风控">
          <el-switch v-model="form.correlation_control_enabled" />
        </el-form-item>
        <el-form-item label="同向最大持仓">
          <el-input-number v-model="form.max_same_direction" :min="1" :max="10" />
        </el-form-item>

        <el-divider content-position="left">监控币种</el-divider>
        <el-form-item label="币种列表">
          <el-select
            v-model="form.symbols"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入币种"
            style="width: 100%"
          >
            <el-option label="BTC" value="BTC-USDT-SWAP" />
            <el-option label="ETH" value="ETH-USDT-SWAP" />
            <el-option label="SOL" value="SOL-USDT-SWAP" />
            <el-option label="DOGE" value="DOGE-USDT-SWAP" />
            <el-option label="XRP" value="XRP-USDT-SWAP" />
            <el-option label="TRX" value="TRX-USDT-SWAP" />
            <el-option label="LTC" value="LTC-USDT-SWAP" />
            <el-option label="BCH" value="BCH-USDT-SWAP" />
            <el-option label="FIL" value="FIL-USDT-SWAP" />
          </el-select>
        </el-form-item>

        <el-form-item label="启用状态">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRiskConfigs, createRiskConfig, updateRiskConfig, deleteRiskConfig } from '@/api/riskConfig'

const loading = ref(false)
const configs = ref([])
const dialogVisible = ref(false)
const isEdit = ref(false)
const currentId = ref(null)

const form = reactive({
  name: '默认风控',
  leverage: 20,
  position_percent: 0.20,
  max_daily_loss_percent: 0.06,
  max_positions: 4,
  activation_percent: 0.05,
  callback_ratio: 0.45,
  profit_tiers: [],
  batch_sizes: [0.50, 0.30, 0.20],
  time_stop_enabled: true,
  max_hold_hours: 48,
  time_stop_reduce_ratio: 0.50,
  atr_stop_enabled: true,
  atr_period: 14,
  atr_multiplier: 2.0,
  max_trades_per_day: 10,
  cooldown_after_loss: 2,
  correlation_control_enabled: true,
  max_same_direction: 2,
  symbols: ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
  is_active: true
})

async function fetchConfigs() {
  loading.value = true
  try {
    const res = await getRiskConfigs({ page: 1, page_size: 50 })
    if (res.data.code === 200) {
      configs.value = res.data.data.items
    }
  } catch (e) {
    ElMessage.error('获取配置失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    name: '默认风控',
    leverage: 20,
    position_percent: 0.20,
    max_daily_loss_percent: 0.06,
    max_positions: 4,
    activation_percent: 0.05,
    callback_ratio: 0.45,
    profit_tiers: [],
    batch_sizes: [0.50, 0.30, 0.20],
    time_stop_enabled: true,
    max_hold_hours: 48,
    time_stop_reduce_ratio: 0.50,
    atr_stop_enabled: true,
    atr_period: 14,
    atr_multiplier: 2.0,
    max_trades_per_day: 10,
    cooldown_after_loss: 2,
    correlation_control_enabled: true,
    max_same_direction: 2,
    symbols: ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
    is_active: true
  })
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
  Object.assign(form, JSON.parse(JSON.stringify(row)))
  dialogVisible.value = true
}

async function handleSubmit() {
  try {
    if (isEdit.value) {
      await updateRiskConfig(currentId.value, form)
      ElMessage.success('更新成功')
    } else {
      await createRiskConfig(form)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchConfigs()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm('确定删除该风控配置？', '提示', { type: 'warning' })
    await deleteRiskConfig(row.id)
    ElMessage.success('删除成功')
    fetchConfigs()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

onMounted(fetchConfigs)
</script>

<style scoped lang="scss">
.risk-page {
  padding: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.sym-tag {
  margin-right: 4px;
}
.more {
  color: var(--text-secondary);
  font-size: 12px;
}
.risk-form {
  max-height: 600px;
  overflow-y: auto;
  padding-right: 8px;
  .unit {
    margin-left: 8px;
    color: var(--text-secondary);
  }
}
</style>
