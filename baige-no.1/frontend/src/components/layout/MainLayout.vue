<template>
  <el-container class="layout-container">
    <Sidebar />
    <el-container direction="vertical">
      <Navbar />
      <TagsView v-if="appStore.tagsView" />
      <el-main class="main-content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import Sidebar from './Sidebar.vue'
import Navbar from './Navbar.vue'
import TagsView from './TagsView.vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
</script>

<style scoped lang="scss">
.main-content {
  padding: 0;
  background: var(--bg-primary);
  overflow-y: auto;
}
</style>
