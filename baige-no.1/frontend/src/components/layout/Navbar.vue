<template>
  <el-header class="navbar">
    <div class="left">
      <el-icon class="hamburger" @click="appStore.toggleSidebar()">
        <Fold v-if="!appStore.sidebarCollapsed" />
        <Expand v-else />
      </el-icon>
      <Breadcrumb />
    </div>
    <div class="right">
      <el-tooltip content="消息中心" placement="bottom">
        <div class="icon-btn" @click="$router.push('/message')">
          <el-badge :value="unreadCount" :hidden="unreadCount === 0" type="primary">
            <el-icon size="18"><Bell /></el-icon>
          </el-badge>
        </div>
      </el-tooltip>
      <el-dropdown @command="handleCommand">
        <div class="avatar-wrapper">
          <el-avatar :size="32" :src="userStore.avatar || defaultAvatar" />
          <span class="username">{{ userStore.username }}</span>
          <el-icon><ArrowDown /></el-icon>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">
              <el-icon><User /></el-icon> 个人中心
            </el-dropdown-item>
            <el-dropdown-item command="settings">
              <el-icon><Setting /></el-icon> 系统设置
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">
              <el-icon><SwitchButton /></el-icon> 退出登录
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </el-header>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useAppStore } from '@/stores/app'
import Breadcrumb from './Breadcrumb.vue'
import { getUnreadCount } from '@/api/message'

const router = useRouter()
const userStore = useUserStore()
const appStore = useAppStore()
const unreadCount = ref(0)
const defaultAvatar = 'https://cube.elemecdn.com/3/7c/3ea6beec64369c2642b92c6726f1epng.png'

let timer = null

async function fetchUnread() {
  try {
    const res = await getUnreadCount()
    unreadCount.value = res.data || 0
  } catch {
    // ignore
  }
}

function handleCommand(command) {
  if (command === 'profile') {
    router.push('/profile')
  } else if (command === 'settings') {
    ElMessage.info('设置功能开发中')
  } else if (command === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    }).then(() => {
      userStore.logout()
      router.push('/login')
      ElMessage.success('已退出登录')
    })
  }
}

onMounted(() => {
  fetchUnread()
  timer = setInterval(fetchUnread, 30000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped lang="scss">
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--header-bg);
  border-bottom: 1px solid var(--border-color);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
  height: 64px;
  
  .left {
    display: flex;
    align-items: center;
    
    .hamburger {
      font-size: 20px;
      cursor: pointer;
      margin-right: 16px;
      color: var(--text-secondary);
      padding: 8px;
      border-radius: 8px;
      transition: all 0.2s;
      
      &:hover {
        color: var(--accent-blue);
        background: var(--bg-hover);
      }
    }
  }
  
  .right {
    display: flex;
    align-items: center;
    gap: 16px;
    
    .icon-btn {
      cursor: pointer;
      color: var(--text-secondary);
      padding: 8px;
      border-radius: 8px;
      transition: all 0.2s;
      
      &:hover {
        color: var(--accent-blue);
        background: var(--bg-hover);
      }
    }
    
    .avatar-wrapper {
      display: flex;
      align-items: center;
      cursor: pointer;
      gap: 8px;
      padding: 4px 12px;
      border-radius: 8px;
      transition: all 0.2s;
      
      &:hover {
        background: var(--bg-hover);
      }
      
      .username {
        font-size: 14px;
        color: var(--text-secondary);
      }
    }
  }
}
</style>
