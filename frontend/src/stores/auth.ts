import { defineStore } from 'pinia'
import { ref } from 'vue'
import apiClient from '@/api/client'

const TOKEN_KEY = 'auth_token'

export const useAuthStore = defineStore('auth', () => {
  const authEnabled = ref(false)
  const isAuthenticated = ref(false)
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))

  /**
   * 查询后端鉴权是否启用，并验证本地 token 的有效性。
   * 在路由守卫中每次初始化时调用一次。
   */
  async function checkStatus(): Promise<void> {
    try {
      const { data } = await apiClient.get('/auth/status')
      authEnabled.value = data.enabled

      if (!data.enabled) {
        // 鉴权关闭，直接放行
        isAuthenticated.value = true
        return
      }

      // 有本地 token → 乐观认为已登录（后端 401 会触发拦截器清除）
      isAuthenticated.value = !!token.value
    } catch {
      // 无法连接后端时不阻塞，保持当前状态
      isAuthenticated.value = !!token.value
    }
  }

  async function login(password: string): Promise<void> {
    const { data } = await apiClient.post('/auth/login', { password })
    token.value = data.token
    localStorage.setItem(TOKEN_KEY, data.token)
    isAuthenticated.value = true
  }

  async function logout(): Promise<void> {
    try {
      await apiClient.post('/auth/logout')
    } finally {
      token.value = null
      localStorage.removeItem(TOKEN_KEY)
      isAuthenticated.value = false
    }
  }

  return { authEnabled, isAuthenticated, token, checkStatus, login, logout }
})
