<template>
  <div class="signals-page">
    <!-- 信号扫描 -->
    <el-card class="scan-card">
      <template #header>
        <div class="card-header">
          <span>🎯 信号扫描</span>
          <el-button type="primary" :loading="scanning" @click="handleScanBatch">
            批量扫描
          </el-button>
        </div>
      </template>

      <div class="scan-row">
        <el-select
          v-model="selectedSymbols"
          multiple
          collapse-tags
          placeholder="选择币种"
          style="width: 400px"
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
      </div>

      <!-- 扫描结果 -->
      <el-row :gutter="16" v-if="scanResults.length > 0" class="results-row">
        <el-col :span="8" v-for="res in scanResults" :key="res.symbol">
          <el-card class="signal-result-card" shadow="hover">
            <div class="result-title">{{ res.symbol.split('-')[0] }}</div>
            
            <!-- 清算信号 -->
            <div v-if="res.liquidation" class="signal-item">
              <div class="signal-header">
                <el-tag size="small" type="warning">清算热力图</el-tag>
                <el-tag size="small" :type="getSignalType(res.liquidation.signal)">
                  {{ res.liquidation.signal }}
                </el-tag>
              </div>
              <div class="signal-detail">
                <div>强度: {{ '★'.repeat(res.liquidation.strength || 1) }}</div>
                <div>距下方: {{ res.liquidation.distance_to_long?.toFixed(2) }}%</div>
                <div>距上方: {{ res.liquidation.distance_to_short?.toFixed(2) }}%</div>
                <div class="reason">{{ res.liquidation.reason }}</div>
              </div>
            </div>

            <!-- 背驰信号 -->
            <div v-if="res.divergence" class="signal-item">
              <div class="signal-header">
                <el-tag size="small" type="info">摩尔缠论</el-tag>
                <el-tag size="small" :type="getSignalType(res.divergence.signal)">
                  {{ res.divergence.signal }}
                </el-tag>
              </div>
              <div class="signal-detail">
                <div v-if="res.divergence.confidence">置信度: {{ res.divergence.confidence }}%</div>
                <div class="reason">{{ res.divergence.reason }}</div>
              </div>
            </div>

            <!-- 宏观信号 -->
            <div v-if="res.macro" class="signal-item">
              <div class="signal-header">
                <el-tag size="small" type="success">宏观过滤</el-tag>
                <el-tag size="small">{{ res.macro.risk_level }}</el-tag>
              </div>
              <div class="signal-detail">
                <div>建议仓位: {{ (res.macro.position_pct * 100).toFixed(0) }}%</div>
                <div>最大持仓: {{ res.macro.max_positions }}个</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </el-card>

    <!-- 历史信号记录 -->
    <el-card class="history-card">
      <template #header>
        <span>📋 历史信号记录</span>
      </template>
      <el-table :data="signals" stripe size="small" v-loading="loading">
        <el-table-column prop="symbol" label="币种" width="120" />
        <el-table-column prop="signal_type" label="信号类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="getTypeTag(row.signal_type)">
              {{ formatSignalType(row.signal_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="direction" label="方向" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="getSignalType(row.direction)">
              {{ row.direction }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="strength" label="强度" width="80">
          <template #default="{ row }">{{ '★'.repeat(row.strength || 1) }}</template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" width="90">
          <template #default="{ row }">{{ row.confidence }}%</template>
        </el-table-column>
        <el-table-column prop="current_price" label="价格" width="110">
          <template #default="{ row }">{{ row.current_price || '-' }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
        <el-table-column prop="is_active" label="状态" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? '有效' : '失效' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSignals, scanBatchSignals } from '@/api/signal'

const loading = ref(false)
const scanning = ref(false)
const signals = ref([])
const scanResults = ref([])
const selectedSymbols = ref(['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'])

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0
})

async function fetchSignals() {
  loading.value = true
  try {
    const res = await getSignals({ page: pagination.page, page_size: pagination.page_size })
    if (res.data.code === 200) {
      signals.value = res.data.data.items
      pagination.total = res.data.data.total
    }
  } catch (e) {
    ElMessage.error('获取信号记录失败')
  } finally {
    loading.value = false
  }
}

async function handleScanBatch() {
  if (selectedSymbols.value.length === 0) {
    ElMessage.warning('请选择至少一个币种')
    return
  }
  scanning.value = true
  try {
    const res = await scanBatchSignals(selectedSymbols.value)
    if (res.data.code === 200) {
      scanResults.value = res.data.data
      ElMessage.success(`扫描完成，共 ${scanResults.value.length} 个币种`)
    }
  } catch (e) {
    ElMessage.error('扫描失败')
  } finally {
    scanning.value = false
  }
}

function handlePageChange(val) {
  pagination.page = val
  fetchSignals()
}

function getSignalType(signal) {
  if (!signal) return 'info'
  const map = {
    'LONG': 'success', 'LONG_BIAS': 'success',
    'SHORT': 'danger', 'SHORT_BIAS': 'danger',
    'CAUTION': 'warning', 'BEARISH': 'danger',
    'BULLISH': 'success', 'NEUTRAL': 'info',
    'FILTER': 'success'
  }
  return map[signal] || 'info'
}

function getTypeTag(type) {
  const map = {
    'liquidation': 'warning',
    'divergence': 'info',
    'macro': 'success',
    'trendline': 'primary',
    'manual': ''
  }
  return map[type] || ''
}

function formatSignalType(type) {
  const map = {
    'liquidation': '清算热力图',
    'divergence': '缠论背驰',
    'macro': '宏观过滤',
    'trendline': '趋势线',
    'manual': '手动'
  }
  return map[type] || type
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

onMounted(fetchSignals)
</script>

<style scoped lang="scss">
.signals-page {
  padding: 16px;
}
.scan-card {
  margin-bottom: 16px;
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .scan-row {
    margin-bottom: 16px;
  }
  .results-row {
    margin-top: 16px;
  }
}
.signal-result-card {
  margin-bottom: 12px;
  .result-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 12px;
    text-align: center;
  }
  .signal-item {
    margin-bottom: 12px;
    padding: 8px;
    background: var(--bg-primary);
    border-radius: 6px;
    .signal-header {
      display: flex;
      gap: 8px;
      margin-bottom: 6px;
    }
    .signal-detail {
      font-size: 12px;
      color: var(--text-secondary);
      line-height: 1.6;
      .reason {
        margin-top: 4px;
        color: var(--text-primary);
      }
    }
  }
}
.history-card {
  .pagination {
    margin-top: 12px;
    justify-content: flex-end;
  }
}
</style>
