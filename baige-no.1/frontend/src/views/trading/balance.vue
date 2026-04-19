<template>
  <div class="page-container">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>账户资产</span>
              <el-button size="small" @click="fetchData" :loading="loading"><el-icon><Refresh /></el-icon> 刷新</el-button>
            </div>
          </template>
          <el-table :data="balanceList" v-loading="loading" border stripe>
            <el-table-column prop="asset" label="币种" width="120" />
            <el-table-column prop="free" label="可用" min-width="150" />
            <el-table-column prop="locked" label="冻结" min-width="150" />
            <el-table-column prop="total" label="总计" min-width="150">
              <template #default="{ row }">
                <strong>{{ row.total }}</strong>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card shadow="never">
          <template #header><span>资产分布</span></template>
          <v-chart :option="chartOption" autoresize style="height: 320px;" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getBalance } from '@/api/market'

use([CanvasRenderer, PieChart, LegendComponent, TooltipComponent])

const loading = ref(false)
const balanceList = ref([])

const chartOption = ref({
  tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
  legend: { orient: 'vertical', left: 'left' },
  series: [{
    type: 'pie',
    radius: ['40%', '70%'],
    avoidLabelOverlap: false,
    itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
    label: { show: false, position: 'center' },
    emphasis: { label: { show: true, fontSize: 20, fontWeight: 'bold' } },
    data: [],
  }],
})

async function fetchData() {
  loading.value = true
  try {
    const res = await getBalance()
    balanceList.value = res.data
    chartOption.value.series[0].data = res.data.map(b => ({ name: b.asset, value: b.total }))
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

onMounted(fetchData)
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
