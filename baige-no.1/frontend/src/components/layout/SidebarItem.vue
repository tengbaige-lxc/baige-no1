<template>
  <div v-if="!item.meta?.hidden">
    <template v-if="hasOneShowingChild(item.children, item) && (!onlyOneChild.children || onlyOneChild.noShowingChildren)">
      <el-menu-item :index="resolvePath(onlyOneChild.path)">
        <el-icon v-if="onlyOneChild.meta?.icon"><component :is="onlyOneChild.meta.icon" /></el-icon>
        <template #title>{{ onlyOneChild.meta?.title }}</template>
      </el-menu-item>
    </template>
    <el-sub-menu v-else :index="resolvePath(item.path)">
      <template #title>
        <el-icon v-if="item.meta?.icon"><component :is="item.meta.icon" /></el-icon>
        <span>{{ item.meta?.title }}</span>
      </template>
      <SidebarItem
        v-for="child in item.children"
        :key="child.path"
        :item="child"
        :base-path="item.path"
      />
    </el-sub-menu>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { isExternal } from '@/utils/validate'

const props = defineProps({
  item: { type: Object, required: true },
  basePath: { type: String, default: '' },
})

const onlyOneChild = ref(null)

function hasOneShowingChild(children = [], parent) {
  const showingChildren = children.filter((item) => !item.meta?.hidden)
  if (showingChildren.length === 0) {
    onlyOneChild.value = { ...parent, path: parent.path || '', noShowingChildren: true }
    return true
  }
  if (showingChildren.length === 1) {
    onlyOneChild.value = showingChildren[0]
    return true
  }
  return false
}

function resolvePath(routePath) {
  if (isExternal(routePath)) return routePath
  if (routePath.startsWith('/')) return routePath
  
  // Build full path from basePath + routePath
  if (props.basePath && routePath) {
    return '/' + props.basePath + '/' + routePath
  }
  if (props.basePath) {
    return '/' + props.basePath
  }
  return '/' + routePath
}
</script>
