<template>
  <div class="trade-page">
    <!-- 顶部标题栏 -->
    <div class="trade-header-bar">
      <div class="symbol-select">
        <el-select v-model="form.symbol" size="default" style="width: 180px;" @change="handleSymbolChange">
          <el-option v-for="s in symbolList" :key="s" :label="s + ' 永续'" :value="s" />
        </el-select>
        <el-tag type="warning" size="small" style="margin-left: 8px;">永续</el-tag>
      </div>
      <div class="current-price" :class="marketStore.priceChange >= 0 ? 'up' : 'down'">
        {{ marketStore.currentPrice.toFixed(2) }}
        <span class="price-change">{{ marketStore.priceChange >= 0 ? '+' : '' }}{{ marketStore.priceChange.toFixed(2) }}%</span>
      </div>
    </div>

    <el-row :gutter="12" class="main-row">
      <!-- 左侧：交易面板 -->
      <el-col :xs="24" :lg="7">
        <el-card shadow="never" class="trade-panel">
          <!-- 开仓/平仓切换 -->
          <div class="tab-switch">
            <div class="tab-item" :class="{ active: tradeTab === 'open' }" @click="tradeTab = 'open'">开仓</div>
            <div class="tab-item" :class="{ active: tradeTab === 'close' }" @click="tradeTab = 'close'">平仓</div>
          </div>

          <!-- 全仓/逐仓 + 杠杆 -->
          <div class="mode-row">
            <el-select v-model="form.margin_mode" size="small" style="width: 100px;">
              <el-option label="全仓" value="cross" />
              <el-option label="逐仓" value="isolated" />
            </el-select>
            <el-select v-model="form.leverage" size="small" style="width: 80px;">
              <el-option v-for="l in leverageList" :key="l" :label="l + 'x'" :value="l" />
            </el-select>
          </div>

          <!-- 委托类型 -->
          <div class="order-type-row">
            <el-select v-model="form.order_type" size="small" style="width: 100%;">
              <el-option label="限价委托" value="LIMIT" />
              <el-option label="市价委托" value="MARKET" />
            </el-select>
          </div>

          <!-- 价格 -->
          <div class="input-row" v-if="form.order_type === 'LIMIT'">
            <div class="input-label">价格 (USDT)</div>
            <el-input v-model="form.price" type="number" :step="0.01" :placeholder="marketStore.orderbook.askPrice ? marketStore.orderbook.askPrice.toFixed(2) : '0'">
              <template #append>USDT</template>
            </el-input>
            <div class="price-tags">
              <el-tag size="small" @click="form.price = marketStore.orderbook.bidPrice">对手价1</el-tag>
              <el-tag size="small" @click="form.price = marketStore.currentPrice">最优价</el-tag>
            </div>
          </div>

          <!-- 数量 -->
          <div class="input-row">
            <div class="input-label">数量 (USDT)</div>
            <el-input v-model="form.quantity" type="number" :step="1" placeholder="0">
              <template #append>USDT</template>
            </el-input>
            <el-slider v-model="quantityPct" :max="100" :step="25" show-stops @change="handleQtySlider" />
          </div>

          <!-- 可用 & 可开 -->
          <div class="info-row">
            <div class="info-item">
              <span class="label">可用</span>
              <span class="value">{{ marketStore.balance.toFixed(2) }} USDT</span>
            </div>
            <div class="info-item">
              <span class="label">可开多</span>
              <span class="value">{{ maxLong.toFixed(2) }} USDT</span>
            </div>
            <div class="info-item">
              <span class="label">可开空</span>
              <span class="value">{{ maxShort.toFixed(2) }} USDT</span>
            </div>
          </div>

          <!-- 按钮 -->
          <div class="btn-row">
            <el-button
              type="success"
              size="large"
              class="long-btn"
              :loading="loading"
              @click="handleSubmit('BUY')"
            >
              {{ tradeTab === 'open' ? '开多' : '平空' }} {{ form.leverage }}x
            </el-button>
            <el-button
              type="danger"
              size="large"
              class="short-btn"
              :loading="loading"
              @click="handleSubmit('SELL')"
            >
              {{ tradeTab === 'open' ? '开空' : '平多' }} {{ form.leverage }}x
            </el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 中间：K线图 -->
      <el-col :xs="24" :lg="11">
        <el-card shadow="never" class="kline-card">
          <template #header>
            <div class="kline-header">
              <el-radio-group v-model="klineInterval" size="small" @change="fetchKline">
                <el-radio-button label="1m">1分</el-radio-button>
                <el-radio-button label="5m">5分</el-radio-button>
                <el-radio-button label="15m">15分</el-radio-button>
                <el-radio-button label="1H">1时</el-radio-button>
                <el-radio-button label="4H">4时</el-radio-button>
                <el-radio-button label="1D">1日</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <v-chart :option="klineOption" autoresize style="height: 460px;" />
        </el-card>
      </el-col>

      <!-- 右侧：盘口 -->
      <el-col :xs="24" :lg="6">
        <el-card shadow="never" class="orderbook-card">
          <template #header>
            <div class="card-header">
              <span>盘口</span>
              <span class="fund-rate">资金费率 0.008%</span>
            </div>
          </template>
          <div class="orderbook">
            <div class="ob-header">
              <span>价格(USDT)</span>
              <span>数量</span>
            </div>
            <!-- 卖盘 -->
            <div class="ob-row ask" v-for="(item, idx) in asks" :key="'ask'+idx">
              <span class="price">{{ item[0].toFixed(2) }}</span>
              <span class="qty">{{ item[1].toFixed(4) }}</span>
              <div class="depth-bar" :style="{ width: (item[1] / maxDepth * 100) + '%', background: 'rgba(248,81,73,0.12)' }"></div>
            </div>
            <!-- 中间价格 -->
            <div class="ob-middle" :class="marketStore.priceChange >= 0 ? 'up' : 'down'">
              {{ marketStore.currentPrice.toFixed(2) }}
            </div>
            <!-- 买盘 -->
            <div class="ob-row bid" v-for="(item, idx) in bids" :key="'bid'+idx">
              <span class="price">{{ item[0].toFixed(2) }}</span>
              <span class="qty">{{ item[1].toFixed(4) }}</span>
              <div class="depth-bar" :style="{ width: (item[1] / maxDepth * 100) + '%', background: 'rgba(63,185,80,0.12)' }"></div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getKlines } from '@/api/market'
import { placeOrder } from '@/api/trade'
import { useMarketStore } from '@/stores/market'

use([CanvasRenderer, CandlestickChart, GridComponent, TooltipComponent, DataZoomComponent])

const marketStore = useMarketStore()
const symbolList = ref(['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'DOGE-USDT', 'XRP-USDT'])
const tradeTab = ref('open')
const loading = ref(false)
const quantityPct = ref(0)
const klineInterval = ref('5m')

const form = ref({
  symbol: 'SOL-USDT',
  order_type: 'LIMIT',
  price: null,
  quantity: 0,
  market_type: 'FUTURES',
  margin_mode: 'cross',
  leverage: 20,
})

const leverageList = [1, 2, 3, 5, 10, 20, 50, 100]

const maxLong = computed(() => {
  if (!marketStore.currentPrice || marketStore.currentPrice <= 0) return 0
  return marketStore.balance * form.value.leverage
})

const maxShort = computed(() => {
  if (!marketStore.currentPrice || marketStore.currentPrice <= 0) return 0
  return marketStore.balance * form.value.leverage
})

const asks = computed(() => {
  const list = [...(marketStore.orderbook.asks || [])].reverse()
  return list.slice(0, 5)
})

const bids = computed(() => {
  return (marketStore.orderbook.bids || []).slice(0, 5)
})

const maxDepth = computed(() => {
  const all = [...asks.value, ...bids.value]
  if (!all.length) return 1
  return Math.max(...all.map(i => i[1]))
})

const klineOption = ref({
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  grid: { left: '3%', right: '3%', bottom: '10%', top: '5%', containLabel: true },
  dataZoom: [{ type: 'inside', start: 70, end: 100 }, { type: 'slider', start: 70, end: 100, bottom: 0 }],
  xAxis: { type: 'category', data: [], scale: true },
  yAxis: { scale: true, splitLine: { show: true, lineStyle: { color: '#30363d' } } },
  series: [{
    type: 'candlestick',
    data: [],
    itemStyle: {
      color: '#f85149',
      color0: '#3fb950',
      borderColor: '#f85149',
      borderColor0: '#3fb950',
    },
  }],
})

let klineTimer = null

async function fetchKline() {
  try {
    const res = await getKlines(form.value.symbol, { interval: klineInterval.value, limit: 200 })
    const klines = res.data
    const times = klines.map(k => {
      const d = new Date(k.time)
      return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
    })
    const data = klines.map(k => [k.open, k.close, k.low, k.high])
    klineOption.value.xAxis.data = times
    klineOption.value.series[0].data = data
  } catch {
    // ignore
  }
}

function handleQtySlider(val) {
  if (!val || val <= 0) {
    form.value.quantity = 0
    return
  }
  const max = tradeTab.value === 'open' ? maxLong.value : marketStore.balance
  form.value.quantity = Math.floor((max * val / 100) * 100) / 100
}

function handleSymbolChange() {
  marketStore.init(form.value.symbol)
  fetchKline()
}

async function handleSubmit(side) {
  if (!form.value.quantity || form.value.quantity <= 0) {
    ElMessage.warning('请输入数量')
    return
  }
  if (form.value.order_type === 'LIMIT' && (!form.value.price || form.value.price <= 0)) {
    ElMessage.warning('请输入价格')
    return
  }

  loading.value = true
  try {
    await placeOrder({
      symbol: form.value.symbol,
      side: side,
      order_type: form.value.order_type,
      quantity: form.value.quantity,
      price: form.value.order_type === 'LIMIT' ? form.value.price : null,
      market_type: form.value.market_type,
      margin_mode: form.value.margin_mode,
      leverage: form.value.leverage,
    })
    ElMessage.success('委托已提交')
    form.value.quantity = 0
    quantityPct.value = 0
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '下单失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  marketStore.init(form.value.symbol)
  fetchKline()
  klineTimer = setInterval(fetchKline, 30000)
})

onUnmounted(() => {
  marketStore.close()
  if (klineTimer) clearInterval(klineTimer)
})
</script>

<style scoped lang="scss">
.trade-page {
  padding: 16px;
}
.trade-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding: 0 4px;
  .symbol-select {
    display: flex;
    align-items: center;
  }
  .current-price {
    font-size: 22px;
    font-weight: 700;
    .price-change {
      font-size: 14px;
      margin-left: 8px;
      font-weight: 500;
    }
  }
}
.main-row {
  .trade-panel {
    .tab-switch {
      display: flex;
      margin-bottom: 12px;
      border-radius: 4px;
      overflow: hidden;
      border: 1px solid var(--border-color);
      .tab-item {
        flex: 1;
        text-align: center;
        padding: 8px 0;
        cursor: pointer;
        font-size: 14px;
        background: var(--bg-secondary);
        color: var(--text-secondary);
        transition: all 0.2s;
        &.active {
          background: var(--primary-color);
          color: #fff;
          font-weight: 600;
        }
      }
    }
    .mode-row {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
    }
    .order-type-row {
      margin-bottom: 12px;
    }
    .input-row {
      margin-bottom: 12px;
      .input-label {
        font-size: 12px;
        color: var(--text-secondary);
        margin-bottom: 4px;
      }
      .price-tags {
        margin-top: 6px;
        display: flex;
        gap: 6px;
        .el-tag {
          cursor: pointer;
        }
      }
    }
    .info-row {
      margin-bottom: 12px;
      .info-item {
        display: flex;
        justify-content: space-between;
        padding: 4px 0;
        font-size: 13px;
        .label { color: var(--text-secondary); }
        .value { font-weight: 600; color: var(--text-primary); }
      }
    }
    .btn-row {
      display: flex;
      gap: 8px;
      .long-btn, .short-btn {
        flex: 1;
        font-size: 16px;
        font-weight: 700;
      }
    }
  }
  .kline-card {
    .kline-header {
      display: flex;
      justify-content: center;
    }
  }
  .orderbook-card {
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      .fund-rate {
        font-size: 12px;
        color: var(--text-secondary);
      }
    }
    .orderbook {
      .ob-header {
        display: flex;
        justify-content: space-between;
        font-size: 12px;
        color: var(--text-secondary);
        padding: 4px 0;
        margin-bottom: 4px;
      }
      .ob-row {
        display: flex;
        justify-content: space-between;
        padding: 3px 4px;
        font-size: 13px;
        position: relative;
        .price { font-weight: 600; z-index: 1; }
        .qty { z-index: 1; }
        .depth-bar {
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          z-index: 0;
        }
        &.ask {
          .price { color: #f85149; }
        }
        &.bid {
          .price { color: #3fb950; }
        }
      }
      .ob-middle {
        text-align: center;
        font-size: 18px;
        font-weight: 700;
        padding: 8px 0;
        border-top: 1px solid var(--border-color);
        border-bottom: 1px solid var(--border-color);
        margin: 4px 0;
      }
    }
  }
}
.up { color: #f85149; }
.down { color: #3fb950; }
</style>
