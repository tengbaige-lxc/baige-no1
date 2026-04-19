<template>
  <div class="page-container">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <el-select v-model="marketStore.symbol" size="small" style="width: 160px;" @change="handleSymbolChange">
                <el-option v-for="s in symbolList" :key="s" :label="s" :value="s" />
              </el-select>
              <span :class="['price-text', marketStore.priceChange >= 0 ? 'up' : 'down']">
                {{ marketStore.currentPrice.toFixed(2) }}
                <el-icon v-if="marketStore.priceChange >= 0"><ArrowUp /></el-icon>
                <el-icon v-else><ArrowDown /></el-icon>
              </span>
            </div>
          </template>
          <v-chart :option="klineOption" autoresize style="height: 420px;" />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card shadow="never" style="margin-bottom: 16px;">
          <template #header>
            <div class="card-header">
              <span>盘口</span>
              <el-tag size="small" :type="marketStore.connected ? 'success' : 'info'">
                {{ marketStore.connected ? '实时' : '连接中' }}
              </el-tag>
            </div>
          </template>
          <div class="orderbook">
            <div class="ob-row ob-header">
              <span>买一</span><span>价格</span><span>卖一</span>
            </div>
            <div class="ob-row">
              <span class="bid-qty">{{ marketStore.orderbook.bidQty }}</span>
              <span class="price">{{ marketStore.orderbook.bidPrice.toFixed(2) }}</span>
              <span class="ask-qty">{{ marketStore.orderbook.askQty }}</span>
            </div>
            <div class="ob-row">
              <span></span>
              <span class="price">{{ marketStore.orderbook.askPrice.toFixed(2) }}</span>
              <span></span>
            </div>
          </div>
        </el-card>
        <el-card shadow="never">
          <template #header><span>24h 概况</span></template>
          <div class="stats-grid">
            <div class="stat-item">
              <div class="label">最新价</div>
              <div class="value" :class="marketStore.priceChange >= 0 ? 'up' : 'down'">{{ marketStore.currentPrice.toFixed(2) }}</div>
            </div>
            <div class="stat-item">
              <div class="label">24h 涨跌</div>
              <div class="value" :class="marketStore.priceChange >= 0 ? 'up' : 'down'">{{ marketStore.priceChange >= 0 ? '+' : '' }}{{ marketStore.priceChange.toFixed(2) }}%</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import { getKlines } from '@/api/market'
import { useMarketStore } from '@/stores/market'

use([CanvasRenderer, CandlestickChart, GridComponent, TooltipComponent, DataZoomComponent])

const marketStore = useMarketStore()
const symbolList = ref(['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'DOGE-USDT', 'XRP-USDT'])
const klineData = ref([])

let klineTimer = null

const klineOption = ref({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'cross' },
  },
  grid: { left: '3%', right: '3%', bottom: '10%', top: '5%', containLabel: true },
  dataZoom: [{ type: 'inside', start: 50, end: 100 }, { type: 'slider', start: 50, end: 100, bottom: 0 }],
  xAxis: { type: 'category', data: [], scale: true },
  yAxis: { scale: true, splitLine: { show: true, lineStyle: { color: '#eee' } } },
  series: [{
    type: 'candlestick',
    data: [],
    itemStyle: {
      color: '#F56C6C',
      color0: '#67C23A',
      borderColor: '#F56C6C',
      borderColor0: '#67C23A',
    },
  }],
})

async function fetchKline() {
  try {
    const res = await getKlines(marketStore.symbol, { interval: '1h', limit: 100 })
    const klines = res.data
    klineData.value = klines
    const times = klines.map(k => {
      const d = new Date(k.time)
      return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:00`
    })
    const data = klines.map(k => [k.open, k.close, k.low, k.high])
    klineOption.value.xAxis.data = times
    klineOption.value.series[0].data = data
  } catch {
    // ignore
  }
}

function handleSymbolChange() {
  marketStore.init(marketStore.symbol)
  fetchKline()
}

onMounted(() => {
  if (!marketStore.connected) {
    marketStore.init('BTC-USDT')
  }
  fetchKline()
  klineTimer = setInterval(fetchKline, 30000)
})

onUnmounted(() => {
  if (klineTimer) clearInterval(klineTimer)
})
</script>

<style scoped lang="scss">
.page-container {
  padding: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.price-text {
  font-size: 20px;
  font-weight: 600;
  &.up { color: var(--accent-red); }
  &.down { color: var(--accent-green); }
}
.orderbook {
  .ob-row {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #f0f0f0;
    &.ob-header { font-weight: 600; color: #909399; }
    .bid-qty { color: #67C23A; }
    .ask-qty { color: #F56C6C; }
    .price { font-weight: 600; }
  }
}
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  .stat-item {
    text-align: center;
    .label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
    .value { font-size: 18px; font-weight: 600; }
  }
}
.up { color: var(--accent-red); }
.down { color: var(--accent-green); }
</style>
