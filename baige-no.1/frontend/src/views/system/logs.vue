<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="search-form">
        <el-form :inline="true" :model="queryParams">
          <el-form-item label="操作类型">
            <el-input v-model="queryParams.action" placeholder="请输入" clearable />
          </el-form-item>
          <el-form-item label="操作用户">
            <el-input v-model="queryParams.username" placeholder="请输入" clearable />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSearch">查询</el-button>
            <el-button @click="resetQuery">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table :data="logList" v-loading="loading" border stripe>
        <el-table-column type="index" width="50" />
        <el-table-column prop="username" label="操作用户" width="120" />
        <el-table-column prop="action" label="操作类型" min-width="160" />
        <el-table-column prop="method" label="请求方法" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="methodType(row.method)">{{ row.method }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="path" label="请求路径" min-width="200" />
        <el-table-column prop="ip_address" label="IP地址" width="140" />
        <el-table-column prop="status_code" label="状态码" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status_code >= 400 ? 'danger' : 'success'">
              {{ row.status_code }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="操作时间" min-width="160">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="queryParams.page"
          v-model:page-size="queryParams.page_size"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getLogList } from '@/api/log'

const loading = ref(false)
const logList = ref([])
const total = ref(0)

const queryParams = reactive({
  page: 1,
  page_size: 10,
  action: '',
  username: '',
})

function methodType(method) {
  const map = { GET: 'success', POST: 'primary', PUT: 'warning', DELETE: 'danger' }
  return map[method] || 'info'
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function fetchLogs() {
  loading.value = true
  try {
    const res = await getLogList(queryParams)
    logList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function handleSearch() { queryParams.page = 1; fetchLogs() }
function resetQuery() { queryParams.action = ''; queryParams.username = ''; queryParams.page = 1; fetchLogs() }
function handleSizeChange(val) { queryParams.page_size = val; fetchLogs() }
function handleCurrentChange(val) { queryParams.page = val; fetchLogs() }

onMounted(fetchLogs)
</script>

<style scoped lang="scss">
.page-container {
  padding: 20px;
}
</style>
