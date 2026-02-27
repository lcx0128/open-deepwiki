import { onUnmounted } from 'vue'
import { useTaskStore } from '@/stores/task'

const MAX_RECONNECT = 5
const RECONNECT_DELAY = 3000

interface Conn {
  es: EventSource | null
  timer: ReturnType<typeof setTimeout> | null
  attempts: number
}

export function useEventSource() {
  const taskStore = useTaskStore()
  const conns = new Map<string, Conn>()

  function connectSSE(taskId: string) {
    _closeOne(taskId)
    conns.set(taskId, { es: null, timer: null, attempts: 0 })
    const base = import.meta.env.VITE_API_BASE_URL || '/api'
    _doConnect(`${base}/tasks/${taskId}/stream`, taskId)
  }

  function _doConnect(url: string, taskId: string) {
    const conn = conns.get(taskId)
    if (!conn) return

    const es = new EventSource(url)
    conn.es = es

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        conn.attempts = 0

        const cur = taskStore.activeTasks.find(t => t.id === taskId)
        taskStore.updateProgress(taskId, {
          status: data.status,
          progressPct: data.progress_pct ?? cur?.progressPct,
          currentStage: data.stage ?? cur?.currentStage,
          filesTotal: data.files_total ?? cur?.filesTotal,
          filesProcessed: data.files_processed ?? cur?.filesProcessed,
          wikiId: data.wiki_id ?? cur?.wikiId,
        })

        if (data.status === 'failed') {
          taskStore.updateProgress(taskId, { errorMsg: data.stage || '处理失败' })
          _closeOne(taskId)
        } else if (['completed', 'cancelled', 'interrupted'].includes(data.status)) {
          _closeOne(taskId)
        }
      } catch {
        // ignore heartbeat / non-JSON
      }
    }

    es.onerror = () => {
      _closeOne(taskId)
      if (conn.attempts < MAX_RECONNECT) {
        conn.attempts++
        conn.timer = setTimeout(() => _doConnect(url, taskId), RECONNECT_DELAY)
      }
    }
  }

  function _closeOne(taskId: string) {
    const conn = conns.get(taskId)
    if (!conn) return
    conn.es?.close()
    conn.es = null
    if (conn.timer) { clearTimeout(conn.timer); conn.timer = null }
  }

  function closeSSE(taskId?: string) {
    if (taskId) {
      _closeOne(taskId)
      conns.delete(taskId)
    } else {
      conns.forEach((_, id) => _closeOne(id))
      conns.clear()
    }
  }

  onUnmounted(() => closeSSE())

  return { connectSSE, closeSSE }
}
