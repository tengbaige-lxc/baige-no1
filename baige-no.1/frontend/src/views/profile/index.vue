<template>
  <div class="page-container">
    <el-row :gutter="20">
      <el-col :xs="24" :lg="8">
        <el-card shadow="hover">
          <div class="profile-header">
            <el-avatar :size="80" :src="userStore.userInfo?.avatar || defaultAvatar" />
            <h3 class="profile-name">{{ userStore.userInfo?.full_name || userStore.userInfo?.username }}</h3>
            <p class="profile-role">{{ userStore.userInfo?.role_name || '普通用户' }}</p>
          </div>
          <div class="profile-info">
            <div class="info-item">
              <span class="label">用户名</span>
              <span class="value">{{ userStore.userInfo?.username }}</span>
            </div>
            <div class="info-item">
              <span class="label">邮箱</span>
              <span class="value">{{ userStore.userInfo?.email }}</span>
            </div>
            <div class="info-item">
              <span class="label">手机</span>
              <span class="value">{{ userStore.userInfo?.phone || '-' }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="16">
        <el-card shadow="hover">
          <template #header><span>编辑资料</span></template>
          <el-form :model="form" label-width="80px">
            <el-form-item label="昵称">
              <el-input v-model="form.full_name" />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input v-model="form.email" />
            </el-form-item>
            <el-form-item label="手机">
              <el-input v-model="form.phone" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleSave">保存</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const defaultAvatar = 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png'

const form = reactive({
  full_name: userStore.userInfo?.full_name || '',
  email: userStore.userInfo?.email || '',
  phone: userStore.userInfo?.phone || '',
})

function handleSave() {
  ElMessage.success('保存成功（演示）')
}
</script>

<style scoped lang="scss">
.page-container {
  padding: 20px;
  .profile-header {
    text-align: center;
    padding: 20px 0;
    .profile-name {
      margin-top: 12px;
      font-size: 18px;
      font-weight: 600;
    }
    .profile-role {
      margin-top: 4px;
      font-size: 14px;
      color: #909399;
    }
  }
  .profile-info {
    padding: 12px 0;
    .info-item {
      display: flex;
      justify-content: space-between;
      padding: 10px 0;
      border-bottom: 1px solid #f0f0f0;
      .label {
        color: #909399;
      }
      .value {
        color: #303133;
      }
    }
  }
}
</style>
