import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getToken, setToken, removeToken } from '@/utils/auth'
import request from '@/api/request'

export const useUserStore = defineStore('user', () => {
  const token = ref(getToken())
  const userInfo = ref(null)
  const permissions = ref([])

  const isLoggedIn = computed(() => !!token.value)
  const avatar = computed(() => userInfo.value?.avatar || '')
  const username = computed(() => userInfo.value?.username || '')

  async function login(loginData) {
    const res = await request.post('/login/access-token', loginData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    const { access_token } = res.data
    token.value = access_token
    setToken(access_token)
    return res
  }

  async function getProfile() {
    const res = await request.get('/login/profile')
    userInfo.value = res.data
    permissions.value = res.data.permissions || []
    return res.data
  }

  function logout() {
    token.value = ''
    userInfo.value = null
    permissions.value = []
    removeToken()
  }

  function hasPermission(perm) {
    if (permissions.value.includes('*')) return true
    return permissions.value.includes(perm)
  }

  return {
    token,
    userInfo,
    permissions,
    isLoggedIn,
    avatar,
    username,
    login,
    getProfile,
    logout,
    hasPermission,
  }
})
