<template>
  <div class="backtest-page">
    <!-- 创建回测 -->
    <el-card class="create-card">
      <template #header>
        <span>🧪 创建回测</span>
      </template>
      <el-form :model="form" label-width="120px" inline>
        <el-form-item label="回测名称">
          <el-input v-model="form.name" placeholder="如：BTC底背离回测" style="width: 200px" />
        </el-form-item>
        <el-form-item label="币种">
          <el-select v-model="form.symbol" style="width: 160px">
            <el-option label="BTC" value="BTC-USDT-SWAP" />
            <el-option label="ETH" value="ETH-USDT-SWAP" />
            <el-option label="SOL" value="SOL-USDT-SWAP" />
            <el-option label="DOGE" value="DOGE-USDT-SWAP" />
            <el-option label="XRP" value="XRP-USDT-SWAP" />
          </el-select>
        </el-form-item>
        <el-form-item label="策略类型">
          <el-select v-model="form.strategy_type" style="width: 160px">
            <el-option label="底背离" value="divergence" />
            <el-option label="均线金叉" value="ma_cross" />
            <el-option label="反弹信号" value="rebound" />
            <el-option label="网格交易" value="grid" />
          </el-select>
        </el-form-item>
        <el-form-item label="回测天数">
          <el-input-number v-model="form.days" :min="7" :max="90" />
        </el-form-item>
        <el-form-item label="杠杆">
          <el-input-number v-model="form.leverage" :min="1" :max="125" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="running" @click="handleRun">运行回测</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 回测列表 -->
    <el-card class="list-card">
      <template #header>
        <span>📋 回测任务</span>
      </template>
      <el-table :data="backtests" stripe v-loading="loading" size="small">
        <el-table-column prop="name" label="名称" width="150" />
        <el-table-column prop="symbol" label="币种" width="130" />
        <el-table-column prop="strategy_type" label="策略" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ formatStrategy(row.strategy_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="days" label="天数" width="70" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)" size="small">
              {{ formatStatus(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="total_signals" label="信号数" width="80" />
        <el-table-column prop="win_rate" label="胜率" width="80">
          <template #default="{ row }">{{ row.win_rate }}%</template>
        </el-table-column>
        <el-table-column prop="total_return" label="总盈亏" width="110">
          <template #default="{ row }">
            <span :class="row.total_return >= 0 ? 'positive' : 'negative'">
              {{ formatPnl(row.total_return) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="profit_factor" label="盈亏比" width="80" />
        <el-table-column prop="max_drawdown" label="最大回撤" width="90">
          <template #default="{ row }">{{ formatPnl(row.max_drawdown) }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="handleView(row)">详情</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
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

    <!-- 回测详情弹窗 -->
    <el-dialog v-model="detailDialog" :title="detail.backtest?.name + ' 详情'" width="900px">
      <div v-if="detail.backtest" class="detail-stats">
        <el-row :gutter="16">
          <el-col :span="4" v-for="stat in detailStats" :key="stat.label">
            <div class="stat-box">
              <div class="stat-label">{{ stat.label }}</div>
              <div class="stat-value" :class="stat.class">{{ stat.value }}</div>
            </div>
          </el-col>
        </el-row>
      </div>

      <!-- 每日盈亏图 -->
      <div ref="backtestChartRef" class="chart-container" v-if="detail.trades?.length > 0"></div>

      <el-table :data="detail.trades" stripe size="small" max-height="400">
        <el-table-column prop="entry_time" label="开仓时间" width="160">
          <template #default="{ row }">{{ formatDate(row.entry_time) }}</template>
        </el-table-column>
        <el-table-column prop="entry_price" label="开仓价" width="100" />
        <el-table-column prop="exit_price" label="平仓价" width="100" />
        <el-table-column prop="pnl_usdt" label="盈亏" width="100">
          <template #default="{ row }">
            <span :class="row.pnl_usdt >= 0 ? 'positive' : 'negative'">
              {{ row.pnl_usdt >= 0 ? '+' : '' }}{{ row.pnl_usdt?.toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="pnl_percent" label="盈亏%" width="80">
          <template #default="{ row }">{{ row.pnl_percent }}%</template>
        </el-table-column>
        <el-table-column prop="hold_hours" label="持仓(h)" width="80" />
        <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { getBacktests, createBacktest, getBacktestDetail, deleteBacktest } from '@/api/backtest'

const loading = ref(false)
const running = ref(false)
const backtests = ref([])
const detailDialog = ref(false)
const detail = reactive({ backtest: null, trades: [] })
const backtestChartRef = ref(null)

const form = reactive({
  name: '',
  symbol: 'BTC-USDT-SWAP',
  strategy_type: 'divergence',
  days: 30,
  leverage: 20,
  position_percent: 0.20,
  params: {}
})

const pagination = reactive({
  page: 1,
  page_size: 10,
  total: 0
})

const detailStats = computed(() => {
  const b = detail.backtest
  if (!b) return []
  return [
    { label: '信号数', value: b.total_signals, class: '' },
    { label: '胜率', value: b.win_rate + '%', class: b.win_rate >= 50 ? 'positive' : 'negative' },
    { label: '盈利', value: b.win_count, class: 'positive' },
    { label: '亏损', value: b.loss_count, class: 'negative' },
    { label: '总盈亏', value: formatPnl(b.total_return), class: b.total_return >= 0 ? 'positive' : 'negative' },
    { label: '盈亏比', value: b.profit_factor?.toFixed(2), class: b.profit_factor >= 1 ? 'positive' : 'negative' },
  ]
})

async function fetchBacktests() {
  loading.value = true
  try {
    const res = await getBacktests({ page: pagination.page, page_size: pagination.page_size })
    if (res.data.code === 200) {
      backtests.value = res.data.data.items
      pagination.total = res.data.data.total
    }
  } catch (e) {
    ElMessage.error('获取回测列表失败')
  } finally {
    loading.value = false
  }
}

async function handleRun() {
  if (!form.name) {
    ElMessage.warning('请输入回测名称')
    return
  }
  running.value = true
  try {
    const res = await createBacktest(form)
    if (res.data.code === 200) {
      ElMessage.success('回测任务已创建，正在后台运行')
      // 轮询检查状态
      setTimeout(() => {
        fetchBacktests()
      }, 3000)
    }
  } catch (e) {
    ElMessage.error('创建回测失败')
  } finally {
    running.value = false
  }
}

async function handleView(row) {
  try {
    const res = await getBacktestDetail(row.id)
    if (res.data.code === 200) {
      detail.backtest = res.data.data.backtest
      detail.trades = res.data.data.trades
      detailDialog.value = true
      nextTick(() => renderChart())
    }
  } catch (e) {
    ElMessage.error('获取详情失败')
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm('确定删除该回测任务？', '提示', { type: 'warning' })
    await deleteBacktest(row.id)
    ElMessage.success('删除成功')
    fetchBacktests()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

function renderChart() {
  if (!backtestChartRef.value || !detail.trades?.length) return
  const chart = echarts.init(backtestChartRef.value)
  
  let cum = 0
  const data = detail.trades.map(t => {
    cum += (t.pnl_usdt || 0)
    return {
      name: t.entry_time?.slice(5, 16) || '',
      value: cum
    }
  })
  
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: data.map(d => d.name), axisLine: { lineStyle: { color: '#30363d' } } },
    yAxis: { type: 'value', axisLine: { lineStyle: { color: '#30363d' } }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      type: 'line',
      data: data.map(d => d.value),
      smooth: true,
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(88, 166, 255, 0.3)' },
            { offset: 1, color: 'rgba(88, 166, 255, 0.05)' }
          ]
        }
      },
      lineStyle: { color: '#58a6ff', width: 2 }
    }]
  })
}

function handlePageChange(val) {
  pagination.page = val
  fetchBacktests()
}

function formatStrategy(type) {
  const map = { divergence: '底背离', ma_cross: '均线金叉', rebound: '反弹', grid: '网格' }
  return map[type] || type
}

function formatStatus(status) {
  const map = { pending: '等待中', running: '运行中', completed: '完成', failed: '失败' }
  return map[status] || status
}

function getStatusType(status) {
  const map = { pending: 'info', running: 'warning', completed: 'success', failed: 'danger' }
  return map[status] || ''
}

function formatPnl(val) {
  if (val === undefined || val === null) return '$0.00'
  const prefix = val >= 0 ? '+' : ''
  return prefix + '$' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatDate(dateStr) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

onMounted(fetchBacktests)
</script>

<style scoped lang="scss">
.backtest-page {
  padding: 16px;
}
.create-card {
  margin-bottom: 16px;
}
.list-card {
  .pagination {
    margin-top: 12px;
    justify-content: flex-end;
  }
}
.detail-stats {
  margin-bottom: 16px;
  .stat-box {
    text-align: center;
    padding: 8px;
    background: var(--bg-primary);
    border-radius: 6px;
    .stat-label {
      font-size: 11px;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }
    .stat-value {
      font-size: 16px;
      font-weight: 700;
      color: var(--text-primary);
      &.positive { color: #3fb950; }
      &.negative { color: #f85149; }
    }
  }
}
.chart-container {
  width: 100%;
  height: 240px;
  margin-bottom: 16px;
}
.positive { color: #3fb950; font-weight: 600; }
.negative { color: #f85149; font-weight: 600; }
</style>
