<template>
  <div class="tags-view">
    <el-scrollbar>
      <div class="tags-wrapper">
        <router-link
          v-for="tag in appStore.visitedViews"
          :key="tag.path"
          :to="tag.path"
          :class="['tags-item', { active: isActive(tag) }]"
        >
          {{ tag.title }}
          <el-icon v-if="!isAffix(tag)" class="close-icon" @click.prevent.stop="closeSelectedTag(tag)">
            <Close />
          </el-icon>
        </router-link>
      </div>
    </el-scrollbar>
  </div>
</template>

<script setup>
import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()

function isActive(tag) {
  return tag.path === route.path
}

function isAffix(tag) {
  return tag.meta && tag.meta.affix
}

function addTags() {
  const { name } = route
  if (name && route.meta && route.meta.title) {
    appStore.addVisitedView({
      name: route.name,
      path: route.path,
      title: route.meta.title,
      meta: { ...route.meta },
    })
  }
}

function closeSelectedTag(view) {
  appStore.delVisitedView(view)
}

watch(() => route.path, addTags, { immediate: true })
</script>

<style scoped lang="scss">
.tags-view {
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  
  .tags-wrapper {
    display: flex;
    padding: 6px 12px;
    gap: 8px;
    
    .tags-item {
      display: inline-flex;
      align-items: center;
      height: 32px;
      line-height: 32px;
      padding: 0 14px;
      font-size: 13px;
      color: var(--text-secondary);
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s;
      white-space: nowrap;
      
      &.active {
        background: linear-gradient(135deg, rgba(88, 166, 255, 0.2), rgba(88, 166, 255, 0.1));
        color: var(--accent-blue);
        border-color: rgba(88, 166, 255, 0.3);
        font-weight: 500;
      }
      
      &:hover {
        border-color: var(--accent-blue);
        color: var(--text-primary);
      }
      
      .close-icon {
        margin-left: 8px;
        font-size: 12px;
        width: 16px;
        height: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        transition: all 0.2s;
        
        &:hover {
          background: rgba(248, 81, 73, 0.2);
          color: var(--accent-red);
        }
      }
    }
  }
}
</style>
