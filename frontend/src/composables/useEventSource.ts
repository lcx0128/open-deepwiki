import { ref, onUnmounted } from 'vue'
import { useTaskStore } from '@/stores/task'
import { useRouter } from 'vue-router'

const MAX_RECONNECT_ATTEMPTS = 5
const RECONNECT_DELAY_MS = 3000

export function useEventSource() {
  const taskStore = useTaskStore()
  const router = useRouter()
  let eventSource: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  const reconnectAttempts = ref(0)
  const isConnected = ref(false)

  function connectSSE(taskId: string) {
    closeSSE()
    reconnectAttempts.value = 0

    const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
    const url = `${baseUrl}/tasks/${taskId}/stream`

    _doConnect(url, taskId)
  }

  function _doConnect(url: string, taskId: string) {
    eventSource = new EventSource(url)
    isConnected.value = true

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        // 成功收到消息，重置重连计数
        reconnectAttempts.value = 0
        taskStore.updateProgress({
          status: data.status,
          progressPct: data.progress_pct ?? taskStore.currentTask?.progressPct,
          currentStage: data.stage ?? taskStore.currentTask?.currentStage,
          filesTotal: data.files_total ?? taskStore.currentTask?.filesTotal,
          filesProcessed: data.files_processed ?? taskStore.currentTask?.filesProcessed,
          wikiId: data.wiki_id ?? taskStore.currentTask?.wikiId,
        })

        if (data.status === 'completed') {
          closeSSE()
          // 任务完成，自动跳转到 Wiki 页
          if (taskStore.currentTask?.repoId) {
            router.push({
              name: 'wiki',
              params: { repoId: taskStore.currentTask.repoId },
            })
          }
        } else if (data.status === 'failed') {
          closeSSE()
          taskStore.updateProgress({ errorMsg: data.stage || '处理失败' })
        } else if (data.status === 'cancelled' || data.status === 'interrupted') {
          closeSSE()
        }
      } catch {
        // 忽略非 JSON 消息（心跳）
      }
    }

    eventSource.onerror = () => {
      isConnected.value = false
      closeSSE()
      if (reconnectAttempts.value < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts.value++
        console.log(`SSE 断线，${RECONNECT_DELAY_MS}ms 后重连 (${reconnectAttempts.value}/${MAX_RECONNECT_ATTEMPTS})`)
        reconnectTimer = setTimeout(() => _doConnect(url, taskId), RECONNECT_DELAY_MS)
      } else {
        console.error('SSE 重连次数超限，放弃重连')
      }
    }
  }

  function closeSSE() {
    if (eventSource) {
      eventSource.close()
      eventSource = null
      isConnected.value = false
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  onUnmounted(() => closeSSE())

  return { connectSSE, closeSSE, reconnectAttempts, isConnected }
}
