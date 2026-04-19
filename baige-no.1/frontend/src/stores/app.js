import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const theme = ref('light')
  const tagsView = ref(true)
  const visitedViews = ref([])

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function addVisitedView(view) {
    if (visitedViews.value.some((v) => v.path === view.path)) return
    visitedViews.value.push(view)
  }

  function delVisitedView(view) {
    const index = visitedViews.value.findIndex((v) => v.path === view.path)
    if (index > -1) visitedViews.value.splice(index, 1)
  }

  return {
    sidebarCollapsed,
    theme,
    tagsView,
    visitedViews,
    toggleSidebar,
    addVisitedView,
    delVisitedView,
  }
})
