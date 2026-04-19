<template>
  <div class="page-container">
    <el-row :gutter="20">
      <el-col :xs="24" :lg="8">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>发送消息</span>
            </div>
          </template>
          <el-form :model="msgForm" :rules="msgRules" ref="msgFormRef" label-width="80px">
            <el-form-item label="消息类型">
              <el-radio-group v-model="msgForm.msg_type">
                <el-radio-button label="system">系统</el-radio-button>
                <el-radio-button label="notice">通知</el-radio-button>
                <el-radio-button label="private">私信</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="标题" prop="title">
              <el-input v-model="msgForm.title" placeholder="请输入标题" />
            </el-form-item>
            <el-form-item label="内容" prop="content">
              <el-input v-model="msgForm.content" type="textarea" rows="6" placeholder="请输入内容" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleSend" :loading="sendLoading">
                <el-icon><Promotion /></el-icon> 发送
              </el-button>
              <el-button @click="resetForm">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="16">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>消息列表</span>
              <el-radio-group v-model="filterStatus" size="small" @change="handleFilterChange">
                <el-radio-button label="">全部</el-radio-button>
                <el-radio-button label="unread">未读</el-radio-button>
                <el-radio-button label="read">已读</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <el-timeline>
            <el-timeline-item
              v-for="msg in messageList"
              :key="msg.id"
              :type="msg.status === 'unread' ? 'primary' : 'info'"
              :timestamp="formatDate(msg.created_at)"
            >
              <el-card shadow="hover" :body-style="{ padding: '12px 16px' }">
                <div class="msg-header">
                  <div class="msg-title">
                    <el-tag size="small" :type="msgTypeColor(msg.msg_type)">{{ msgTypeText(msg.msg_type) }}</el-tag>
                    <span class="title-text">{{ msg.title }}</span>
                    <el-tag v-if="msg.status === 'unread'" size="small" type="danger">未读</el-tag>
                  </div>
                  <el-button v-if="msg.status === 'unread'" link type="primary" size="small" @click="markAsRead(msg)">
                    标记已读
                  </el-button>
                </div>
                <div class="msg-content">{{ msg.content }}</div>
                <div class="msg-footer">
                  <span>发送人: {{ msg.sender_name || '系统' }}</span>
                </div>
              </el-card>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="messageList.length === 0" description="暂无消息" />
          <div class="pagination-wrapper">
            <el-pagination
              v-model:current-page="queryParams.page"
              v-model:page-size="queryParams.page_size"
              :page-sizes="[10, 20]"
              :total="total"
              layout="total, prev, pager, next"
              @current-change="handlePageChange"
            />
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getMessageList, createMessage, markRead } from '@/api/message'

const msgFormRef = ref(null)
const sendLoading = ref(false)
const loading = ref(false)
const messageList = ref([])
const total = ref(0)
const filterStatus = ref('')

const msgForm = reactive({
  title: '',
  content: '',
  msg_type: 'system',
})

const msgRules = {
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
  content: [{ required: true, message: '请输入内容', trigger: 'blur' }],
}

const queryParams = reactive({
  page: 1,
  page_size: 10,
  status: '',
})

function msgTypeColor(type) {
  const map = { system: 'danger', notice: 'warning', private: 'success' }
  return map[type] || 'info'
}

function msgTypeText(type) {
  const map = { system: '系统', notice: '通知', private: '私信' }
  return map[type] || type
}

function formatDate(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function fetchMessages() {
  loading.value = true
  try {
    const res = await getMessageList(queryParams)
    messageList.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

async function handleSend() {
  const valid = await msgFormRef.value.validate().catch(() => false)
  if (!valid) return

  sendLoading.value = true
  try {
    await createMessage(msgForm)
    ElMessage.success('发送成功')
    resetForm()
    fetchMessages()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '发送失败')
  } finally {
    sendLoading.value = false
  }
}

function resetForm() {
  msgForm.title = ''
  msgForm.content = ''
  msgForm.msg_type = 'system'
  msgFormRef.value?.resetFields()
}

async function markAsRead(msg) {
  try {
    await markRead(msg.id)
    msg.status = 'read'
    ElMessage.success('已标记为已读')
  } catch {
    ElMessage.error('操作失败')
  }
}

function handleFilterChange(val) {
  queryParams.status = val
  queryParams.page = 1
  fetchMessages()
}

function handlePageChange(val) {
  queryParams.page = val
  fetchMessages()
}

onMounted(fetchMessages)
</script>

<style scoped lang="scss">
.page-container {
  padding: 20px;
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .msg-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    .msg-title {
      display: flex;
      align-items: center;
      gap: 8px;
      .title-text {
        font-weight: 600;
        font-size: 14px;
      }
    }
  }
  .msg-content {
    color: #606266;
    font-size: 13px;
    line-height: 1.6;
    margin-bottom: 8px;
  }
  .msg-footer {
    font-size: 12px;
    color: #909399;
  }
}
</style>
