import axios from 'axios'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
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
