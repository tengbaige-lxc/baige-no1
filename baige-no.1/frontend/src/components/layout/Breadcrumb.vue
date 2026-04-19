<template>
  <el-breadcrumb separator="/">
    <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.path" :to="item.path">
      {{ item.meta.title }}
    </el-breadcrumb-item>
  </el-breadcrumb>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const breadcrumbs = ref([])

function getBreadcrumbs() {
  const matched = route.matched.filter((item) => item.meta && item.meta.title)
  breadcrumbs.value = matched.map((item) => ({
    path: item.path,
    meta: item.meta,
  }))
}

watch(() => route.path, getBreadcrumbs, { immediate: true })
</script>
