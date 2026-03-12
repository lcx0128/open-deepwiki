import axios from 'axios'

const TOKEN_KEY = 'auth_token'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截器：自动附加会话 token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      switch (status) {
        case 400:
          console.error('请求参数错误:', data?.detail || data)
          break
        case 401:
          // token 失效或未登录，清除本地凭证
          localStorage.removeItem(TOKEN_KEY)
          // 访客可访问路由（/、/repos、/wiki/...）不触发强制跳转，
          // 视图层已通过 authStore.isAuthenticated 守卫避免发起需认证的请求；
          // 其他路由（/system、/chat 等）收到 401 则跳登录页。
          {
            const p = window.location.pathname
            const isGuestPath = p === '/' || p === '/repos' || p.startsWith('/wiki/')
            if (!isGuestPath && p !== '/login') {
              window.location.href = '/login'
            }
          }
          break
        case 404:
          console.error('资源不存在:', data?.detail)
          break
        case 409:
          console.warn('资源冲突:', data?.detail)
          break
        case 422:
          console.error('数据校验失败:', data?.detail)
          break
        case 500:
          console.error('服务器内部错误')
          break
      }
    } else if (error.code === 'ECONNABORTED') {
      console.error('请求超时')
    } else if (error.code === 'ERR_NETWORK') {
      console.error('网络连接失败，请检查后端服务是否运行')
    }
    return Promise.reject(error)
  }
)

export default apiClient
