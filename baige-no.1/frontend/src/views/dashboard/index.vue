<template>
  <div class="dashboard">
    <!-- Stats Cards -->
    <el-row :gutter="16" class="stats-row">
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card class="stat-card" shadow="never">
          <div class="stat-icon blue"><el-icon size="28" color="#fff"><User /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.user_count }}</div>
            <div class="stat-label">用户总数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card class="stat-card" shadow="never">
          <div class="stat-icon green"><el-icon size="28" color="#fff"><Message /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.message_count }}</div>
            <div class="stat-label">今日消息</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card class="stat-card" shadow="never">
          <div class="stat-icon orange"><el-icon size="28" color="#fff"><Document /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.log_count }}</div>
            <div class="stat-label">今日操作</div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card class="stat-card" shadow="never">
          <div class="stat-icon purple"><el-icon size="28" color="#fff"><View /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.online_count }}</div>
            <div class="stat-label">在线用户</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Charts & Logs -->
    <el-row :gutter="16" class="content-row">
      <el-col :xs="24" :lg="16">
        <el-card class="dashboard-card" shadow="never">
          <template #header>
            <span>访问趋势</span>
          </template>
          <v-chart :option="chartOption" autoresize style="height: 320px;" />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card class="dashboard-card" shadow="never">
          <template #header>
            <span>最近操作</span>
          </template>
          <el-timeline>
            <el-timeline-item
              v-for="log in recentLogs"
              :key="log.id"
              :type="log.status_code >= 400 ? 'danger' : 'primary'"
              :timestamp="formatTime(log.created_at)"
            >
              <div class="log-item">
                <div class="log-action">{{ log.action }}</div>
                <div class="log-user">{{ log.username }} · {{ log.ip_address }}</div>
              </div>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
    </el-row>

    <!-- Quick Actions -->
    <el-row :gutter="16" class="content-row">
      <el-col :xs="24">
        <el-card shadow="never">
          <template #header><span>快捷入口</span></template>
          <div class="quick-actions">
            <div class="quick-item" @click="$router.push('/system/users')">
              <el-icon size="24" color="#58a6ff"><User /></el-icon>
              <span>用户管理</span>
            </div>
            <div class="quick-item" @click="$router.push('/system/roles')">
              <el-icon size="24" color="#3fb950"><UserFilled /></el-icon>
              <span>角色管理</span>
            </div>
            <div class="quick-item" @click="$router.push('/system/menus')">
              <el-icon size="24" color="#d29922"><Menu /></el-icon>
              <span>菜单管理</span>
            </div>
            <div class="quick-item" @click="$router.push('/message')">
              <el-icon size="24" color="#f85149"><Message /></el-icon>
              <span>消息中心</span>
            </div>
            <div class="quick-item" @click="$router.push('/system/logs')">
              <el-icon size="24" color="#a371f7"><Document /></el-icon>
              <span>操作日志</span>
            </div>
            <div class="quick-item" @click="$router.push('/trading/market')">
              <el-icon size="24" color="#58a6ff"><TrendCharts /></el-icon>
              <span>行情面板</span>
            </div>
            <div class="quick-item" @click="$router.push('/trading/trade')">
              <el-icon size="24" color="#3fb950"><SwitchButton /></el-icon>
              <span>下单交易</span>
            </div>
            <div class="quick-item" @click="$router.push('/strategy/list')">
              <el-icon size="24" color="#d29922"><Cpu /></el-icon>
              <span>策略交易</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getStats, getRecentLogs } from '@/api/dashboard'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const stats = ref({
  user_count: 0,
  message_count: 0,
  log_count: 0,
  online_count: 0,
})
const recentLogs = ref([])

const chartOption = ref({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(22, 27, 34, 0.95)',
    borderColor: '#30363d',
    textStyle: { color: '#e6edf3' },
  },
  legend: {
    data: ['访问量', '用户数'],
    textStyle: { color: '#8b949e' },
  },
  grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
    axisLine: { lineStyle: { color: '#30363d' } },
    axisLabel: { color: '#8b949e' },
  },
  yAxis: {
    type: 'value',
    axisLine: { lineStyle: { color: '#30363d' } },
    axisLabel: { color: '#8b949e' },
    splitLine: { lineStyle: { color: '#21262d' } },
  },
  series: [
    {
      name: '访问量',
      type: 'line',
      smooth: true,
      data: [120, 132, 101, 134, 90, 230, 210],
      itemStyle: { color: '#58a6ff' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(88, 166, 255, 0.3)' },
            { offset: 1, color: 'rgba(88, 166, 255, 0.01)' },
          ],
        },
      },
    },
    {
      name: '用户数',
      type: 'line',
      smooth: true,
      data: [220, 182, 191, 234, 290, 330, 310],
      itemStyle: { color: '#3fb950' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(63, 185, 80, 0.3)' },
            { offset: 1, color: 'rgba(63, 185, 80, 0.01)' },
          ],
        },
      },
    },
  ],
})

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}-${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(async () => {
  try {
    const statsRes = await getStats()
    stats.value = statsRes.data
    const logsRes = await getRecentLogs()
    recentLogs.value = logsRes.data
  } catch {
    // ignore
  }
})
</script>

<style scoped lang="scss">
.dashboard {
  padding: 20px;
  
  .stats-row {
    margin-bottom: 16px;
  }
  
  .content-row {
    margin-bottom: 16px;
  }
  
  .log-item {
    .log-action {
      font-size: 14px;
      color: var(--text-primary);
      font-weight: 500;
    }
    .log-user {
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 2px;
    }
  }
  
  .quick-actions {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    
    .quick-item {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      padding: 20px 28px;
      border-radius: 12px;
      cursor: pointer;
      transition: all 0.3s;
      border: 1px solid var(--border-color);
      background: var(--bg-secondary);
      min-width: 100px;
      
      span {
        font-size: 13px;
        color: var(--text-secondary);
      }
      
      &:hover {
        background: var(--bg-hover);
        border-color: var(--accent-blue);
        transform: translateY(-3px);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
      }
    }
  }
}
</style>
