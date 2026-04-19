<template>
  <div class="performance-page">
    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :span="4" v-for="stat in topStats" :key="stat.label">
        <el-card class="stat-card">
          <div class="stat-label">{{ stat.label }}</div>
          <div class="stat-value" :class="stat.class">{{ stat.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 每日盈亏图表 + 币种统计 -->
    <el-row :gutter="16" class="charts-row">
      <el-col :span="14">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>每日盈亏趋势</span>
              <el-radio-group v-model="periodDays" size="small" @change="fetchData">
                <el-radio-button :label="7">7天</el-radio-button>
                <el-radio-button :label="30">30天</el-radio-button>
                <el-radio-button :label="90">90天</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div ref="pnlChartRef" class="chart-container"></div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card>
          <template #header>
            <span>币种盈亏分布</span>
          </template>
          <div ref="symbolChartRef" class="chart-container"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 币种详细统计 -->
    <el-card class="detail-card">
      <template #header>
        <span>币种详细统计</span>
      </template>
      <el-table :data="symbolStats" stripe size="small">
        <el-table-column prop="symbol" label="币种" width="140" />
        <el-table-column prop="total_trades" label="交易次数" width="100" />
        <el-table-column prop="win_count" label="盈利次数" width="100" />
        <el-table-column prop="loss_count" label="亏损次数" width="100" />
        <el-table-column prop="win_rate" label="胜率" width="100">
          <template #default="{ row }">{{ row.win_rate }}%</template>
        </el-table-column>
        <el-table-column prop="total_pnl" label="总盈亏" width="130">
          <template #default="{ row }">
            <span :class="row.total_pnl >= 0 ? 'positive' : 'negative'">
              {{ formatPnl(row.total_pnl) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="avg_pnl" label="平均盈亏" width="130">
          <template #default="{ row }">
            <span :class="row.avg_pnl >= 0 ? 'positive' : 'negative'">
              {{ formatPnl(row.avg_pnl) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getPerformanceOverview } from '@/api/performance'

const loading = ref(false)
const periodDays = ref(30)
const pnlChartRef = ref(null)
const symbolChartRef = ref(null)

const overview = reactive({
  overall: {
    total_trades: 0, win_count: 0, loss_count: 0, win_rate: 0,
    total_pnl: 0, avg_pnl: 0, avg_win: 0, avg_loss: 0,
    profit_factor: 0, max_drawdown: 0
  },
  symbol_stats: [],
  daily_pnl: []
})

const topStats = computed(() => {
  const o = overview.overall
  return [
    { label: '总交易', value: o.total_trades, class: '' },
    { label: '胜率', value: o.win_rate + '%', class: o.win_rate >= 50 ? 'positive' : 'negative' },
    { label: '总盈亏', value: formatPnl(o.total_pnl), class: o.total_pnl >= 0 ? 'positive' : 'negative' },
    { label: '盈亏比', value: o.profit_factor.toFixed(2), class: o.profit_factor >= 1 ? 'positive' : 'negative' },
    { label: '最大回撤', value: formatPnl(-o.max_drawdown), class: 'negative' },
    { label: '平均盈亏', value: formatPnl(o.avg_pnl), class: o.avg_pnl >= 0 ? 'positive' : 'negative' },
  ]
})

const symbolStats = computed(() => overview.symbol_stats)

async function fetchData() {
  loading.value = true
  try {
    const res = await getPerformanceOverview(periodDays.value)
    if (res.data.code === 200) {
      Object.assign(overview, res.data.data)
      nextTick(() => {
        renderPnlChart()
        renderSymbolChart()
      })
    }
  } catch (e) {
    ElMessage.error('获取绩效数据失败')
  } finally {
    loading.value = false
  }
}

function renderPnlChart() {
  if (!pnlChartRef.value) return
  const chart = echarts.init(pnlChartRef.value)
  const data = overview.daily_pnl || []
  const dates = data.map(d => d.date.slice(5))
  const values = data.map(d => d.pnl)

  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#30363d' } } },
    yAxis: { type: 'value', axisLine: { lineStyle: { color: '#30363d' } }, splitLine: { lineStyle: { color: '#21262d' } } },
    series: [{
      type: 'bar',
      data: values.map(v => ({
        value: v,
        itemStyle: { color: v >= 0 ? '#3fb950' : '#f85149' }
      })),
      barWidth: '60%'
    }]
  })
}

function renderSymbolChart() {
  if (!symbolChartRef.value) return
  const chart = echarts.init(symbolChartRef.value)
  const data = overview.symbol_stats || []

  chart.setOption({
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      data: data.map(d => ({
        name: d.symbol.split('-')[0],
        value: Math.abs(d.total_pnl),
        itemStyle: { color: d.total_pnl >= 0 ? '#3fb950' : '#f85149' }
      })),
      label: { color: '#c9d1d9' }
    }]
  })
}

function formatPnl(val) {
  if (val === undefined || val === null) return '$0.00'
  const prefix = val >= 0 ? '+' : ''
  return prefix + '$' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

onMounted(fetchData)
</script>

<style scoped lang="scss">
.performance-page {
  padding: 16px;
}
.stats-row {
  margin-bottom: 16px;
}
.stat-card {
  text-align: center;
  padding: 8px 0;
  .stat-label {
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 6px;
  }
  .stat-value {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    &.positive { color: #3fb950; }
    &.negative { color: #f85149; }
  }
}
.charts-row {
  margin-bottom: 16px;
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
}
.chart-container {
  width: 100%;
  height: 320px;
}
.detail-card {
  .positive { color: #3fb950; font-weight: 600; }
  .negative { color: #f85149; font-weight: 600; }
}
</style>
