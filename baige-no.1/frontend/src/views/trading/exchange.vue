<template>
  <div class="page-container">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>交易所配置</span>
          <el-button type="primary" @click="handleAdd"><el-icon><Plus /></el-icon> 新增配置</el-button>
        </div>
      </template>

      <el-alert
        title="安全提示"
        description="API Key 将使用 AES 加密存储。建议先在币安测试网（Testnet）测试，确认无误后再使用正式网。"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 16px;"
      />

      <el-table :data="configList" v-loading="loading" border stripe>
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="exchange" label="交易所" width="120" />
        <el-table-column prop="is_testnet" label="测试网" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_testnet ? 'success' : 'danger'">{{ row.is_testnet ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '禁用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="500px">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="100px">
        <el-form-item label="配置名称" prop="name">
          <el-input v-model="form.name" placeholder="如：币安主账户" />
        </el-form-item>
        <el-form-item label="API Key" prop="api_key">
          <el-input v-model="form.api_key" type="password" show-password placeholder="OKX API Key" />
        </el-form-item>
        <el-form-item label="API Secret" prop="api_secret">
          <el-input v-model="form.api_secret" type="password" show-password placeholder="OKX API Secret" />
        </el-form-item>
        <el-form-item label="Passphrase">
          <el-input v-model="form.api_passphrase" type="password" show-password placeholder="OKX API Passphrase" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitLoading">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getExchangeConfigs, createExchangeConfig, updateExchangeConfig, deleteExchangeConfig } from '@/api/exchange'

const loading = ref(false)
const submitLoading = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEdit = ref(false)
const formRef = ref(null)
const configList = ref([])

const form = reactive({
  id: null,
  name: '',
  api_key: '',
  api_secret: '',
  api_passphrase: '',
  is_testnet: false,
  is_active: true,
})

const formRules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  api_key: [{ required: true, message: '请输入 API Key', trigger: 'blur' }],
  api_secret: [{ required: true, message: '请输入 API Secret', trigger: 'blur' }],
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function fetchConfigs() {
  loading.value = true
  try {
    const res = await getExchangeConfigs()
    configList.value = res.data
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.id = null
  form.name = ''
  form.api_key = ''
  form.api_secret = ''
  form.is_testnet = true
  form.is_active = true
}

function handleAdd() {
  resetForm()
  isEdit.value = false
  dialogTitle.value = '新增交易所配置'
  dialogVisible.value = true
}

function handleEdit(row) {
  isEdit.value = true
  dialogTitle.value = '编辑交易所配置'
  Object.assign(form, row)
  form.api_key = ''
  form.api_secret = ''
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitLoading.value = true
  try {
    if (isEdit.value) {
      const data = {}
      if (form.name) data.name = form.name
      if (form.api_key) data.api_key = form.api_key
      if (form.api_secret) data.api_secret = form.api_secret
      if (form.api_passphrase) data.api_passphrase = form.api_passphrase
      data.is_testnet = form.is_testnet
      data.is_active = form.is_active
      await updateExchangeConfig(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createExchangeConfig(form)
      ElMessage.success('配置成功')
    }
    dialogVisible.value = false
    fetchConfigs()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除配置 "${row.name}" 吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    await deleteExchangeConfig(row.id)
    ElMessage.success('删除成功')
    fetchConfigs()
  })
}

onMounted(fetchConfigs)
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
