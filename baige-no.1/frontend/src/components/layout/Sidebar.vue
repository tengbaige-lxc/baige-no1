<template>
  <el-aside :width="appStore.sidebarCollapsed ? '64px' : '220px'" class="sidebar">
    <div class="logo">
      <el-icon size="28" color="#58a6ff"><Promotion /></el-icon>
      <span v-show="!appStore.sidebarCollapsed" class="logo-text">白鸽一号</span>
    </div>
    <el-scrollbar>
      <el-menu
        :default-active="activeMenu"
        :collapse="appStore.sidebarCollapsed"
        :collapse-transition="false"
        router
        background-color="transparent"
        text-color="#8b949e"
        active-text-color="#58a6ff"
      >
        <SidebarItem v-for="route in menuRoutes" :key="route.path" :item="route" :base-path="route.path" />
      </el-menu>
    </el-scrollbar>
  </el-aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import SidebarItem from './SidebarItem.vue'
import { routes } from '@/router/index'

const route = useRoute()
const appStore = useAppStore()

const activeMenu = computed(() => {
  const { meta, path } = route
  if (meta.activeMenu) return meta.activeMenu
  return path
})

const menuRoutes = computed(() => {
  const layout = routes.find((r) => r.path === '/')
  return layout?.children || []
})
</script>

<style scoped lang="scss">
.sidebar {
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border-color);
  transition: width 0.3s;
  
  .logo {
    height: 64px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--sidebar-bg);
    border-bottom: 1px solid var(--border-color);
    
    .logo-text {
      margin-left: 12px;
      color: var(--text-primary);
      font-size: 18px;
      font-weight: 700;
      white-space: nowrap;
      letter-spacing: 1px;
    }
  }
  
  :deep(.el-menu) {
    border-right: none;
    padding: 8px 0;
  }
  
  :deep(.el-sub-menu__title) {
    height: 48px;
    line-height: 48px;
    border-radius: 8px;
    margin: 2px 8px;
  }
  
  :deep(.el-menu-item) {
    height: 44px;
    line-height: 44px;
    border-radius: 8px;
    margin: 2px 8px;
    font-size: 14px;
    
    &.is-active {
      background: linear-gradient(90deg, rgba(88, 166, 255, 0.15), transparent) !important;
      border-left: 3px solid var(--accent-blue);
    }
  }
}
</style>
