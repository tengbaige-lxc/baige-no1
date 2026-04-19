<template>
  <div class="positions-page">
    <!-- 账户概览 -->
    <el-row :gutter="16" class="summary-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-label">USDT 余额</div>
          <div class="stat-value">{{ formatMoney(marketStore.balance) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-label">持仓盈亏</div>
          <div class="stat-value" :class="marketStore.totalPnl >= 0 ? 'positive' : 'negative'">
            {{ formatPnl(marketStore.totalPnl) }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-label">盈亏比例</div>
          <div class="stat-value" :class="marketStore.totalPnl >= 0 ? 'positive' : 'negative'">
            {{ marketStore.totalPnl?.toFixed(2) || '0.00' }}%
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-label">持仓数量</div>
          <div class="stat-value">{{ marketStore.positionCount }} 个</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 持仓列表 -->
    <el-card class="positions-card">
      <template #header>
        <div class="card-header">
          <span>实时持仓监控</span>
          <div class="header-right">
            <el-tag size="small" :type="marketStore.connected ? 'success' : 'info'">
              {{ marketStore.connected ? '实时推送中' : '连接中...' }}
            </el-tag>
            <el-button type="primary" size="small" @click="marketStore.fetchPrivate" :loading="false">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="marketStore.positions.length === 0" description="暂无持仓" />

      <div v-else class="position-list">
        <el-card
          v-for="pos in marketStore.positions"
          :key="pos.symbol"
          class="position-item"
          :class="pos.pnl >= 0 ? 'profit' : 'loss'"
        >
          <div class="pos-header-row">
            <div class="pos-title">
              <span class="coin">{{ pos.coin }}USDT 永续</span>
              <el-tag size="small" :type="pos.side === '多' ? 'success' : 'danger'" effect="dark">{{ pos.side }}</el-tag>
              <el-tag size="small" type="info">{{ pos.margin_mode }}</el-tag>
              <el-tag size="small" type="info">{{ pos.lever }}x</el-tag>
            </div>
            <div class="pos-pnl-summary" :class="pos.pnl >= 0 ? 'positive' : 'negative'">
              <div class="pnl-label">收益额 (USDT)</div>
              <div class="pnl-amount">{{ formatPnl(pos.pnl) }}</div>
              <div class="pnl-pct">{{ pos.pnl >= 0 ? '+' : '' }}{{ pos.pnl_pct?.toFixed(2) || '0.00' }}%</div>
            </div>
          </div>

          <el-divider style="margin: 16px 0;" />

          <el-row :gutter="24">
            <el-col :span="8">
              <div class="info-label">持仓量 (USDT)</div>
              <div class="info-value">{{ formatMoney(pos.notional_usd) }}</div>
            </el-col>
            <el-col :span="8">
              <div class="info-label">保证金 (USDT)</div>
              <div class="info-value">{{ formatMoney(pos.imr) }}</div>
            </el-col>
            <el-col :span="8">
              <div class="info-label">维持保证金率</div>
              <div class="info-value">{{ pos.mgn_ratio?.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) }}%</div>
            </el-col>
          </el-row>

          <el-row :gutter="24" style="margin-top: 16px;">
            <el-col :span="8">
              <div class="info-label">开仓均价</div>
              <div class="info-value">{{ pos.entry }}</div>
            </el-col>
            <el-col :span="8">
              <div class="info-label">标记价格</div>
              <div class="info-value">{{ pos.mark_px }}</div>
            </el-col>
            <el-col :span="8">
              <div class="info-label">预估强平价</div>
              <div class="info-value liq">{{ pos.liq_px || '--' }}</div>
            </el-col>
          </el-row>
        </el-card>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useMarketStore } from '@/stores/market'

const marketStore = useMarketStore()

function formatMoney(val) {
  return val !== undefined ? '$' + val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '$0.00'
}

function formatPnl(val) {
  if (val === undefined) return '$0.00'
  const prefix = val >= 0 ? '+' : ''
  return prefix + '$' + Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

onMounted(() => {
  if (!marketStore.connected) {
    marketStore.init('SOL-USDT')
  }
})

onUnmounted(() => {
  // 由页面切换时统一管理，这里不关闭，避免切换页面断连
})
</script>

<style scoped lang="scss">
.positions-page {
  padding: 16px;
}
.summary-row {
  margin-bottom: 16px;
}
.stat-card {
  text-align: center;
  .stat-label {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 8px;
  }
  .stat-value {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
    &.positive { color: #3fb950; }
    &.negative { color: #f85149; }
  }
}
.positions-card {
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    .header-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
  }
}
.position-list {
  .position-item {
    margin-bottom: 16px;
    border-left: 3px solid #30363d;
    transition: all 0.3s;
    &.profit { border-left-color: #3fb950; }
    &.loss { border-left-color: #f85149; }

    .pos-header-row {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      .pos-title {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        .coin {
          font-size: 16px;
          font-weight: 700;
          color: var(--text-primary);
        }
      }
      .pos-pnl-summary {
        text-align: right;
        .pnl-label {
          font-size: 12px;
          color: var(--text-secondary);
        }
        .pnl-amount {
          font-size: 20px;
          font-weight: 700;
        }
        .pnl-pct {
          font-size: 13px;
          margin-top: 2px;
        }
        &.positive {
          .pnl-amount, .pnl-pct { color: #3fb950; }
        }
        &.negative {
          .pnl-amount, .pnl-pct { color: #f85149; }
        }
      }
    }

    .info-label {
      font-size: 12px;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }
    .info-value {
      font-size: 15px;
      font-weight: 600;
      color: var(--text-primary);
      &.liq { color: #f85149; }
    }
  }
}
</style>
