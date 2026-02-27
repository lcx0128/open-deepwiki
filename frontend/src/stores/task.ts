import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface TaskState {
  id: string
  repoId: string
  type: string
  status: 'pending' | 'cloning' | 'parsing' | 'embedding' | 'generating' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  progressPct: number
  currentStage: string
  filesTotal: number
  filesProcessed: number
  errorMsg: string | null
  wikiId: string | null
}

const TERMINAL = ['completed', 'failed', 'cancelled', 'interrupted']

export const useTaskStore = defineStore('task', () => {
  const activeTasks = ref<TaskState[]>([])
  const taskHistory = ref<TaskState[]>([])

  // backward compat: last added task
  const currentTask = computed(() =>
    activeTasks.value.length > 0 ? activeTasks.value[activeTasks.value.length - 1] : null
  )

  const isProcessing = computed(() =>
    activeTasks.value.some(t => !TERMINAL.includes(t.status))
  )

  const isTerminal = computed(() =>
    activeTasks.value.length > 0 && activeTasks.value.every(t => TERMINAL.includes(t.status))
  )

  function _syncStorage() {
    const ids = activeTasks.value.map(t => t.id)
    if (ids.length > 0) {
      localStorage.setItem('activeTaskIds', JSON.stringify(ids))
      localStorage.setItem('activeTaskId', ids[ids.length - 1])
    } else {
      localStorage.removeItem('activeTaskIds')
      localStorage.removeItem('activeTaskId')
    }
  }

  function setTask(task: TaskState) {
    const idx = activeTasks.value.findIndex(t => t.id === task.id)
    if (idx >= 0) {
      activeTasks.value[idx] = task
    } else {
      activeTasks.value.push(task)
    }
    _syncStorage()
  }

  function updateProgress(taskId: string, data: Partial<TaskState>) {
    const task = activeTasks.value.find(t => t.id === taskId)
    if (task) Object.assign(task, data)
  }

  function clearTask(taskId?: string) {
    if (taskId) {
      const idx = activeTasks.value.findIndex(t => t.id === taskId)
      if (idx >= 0) {
        pushToHistory(activeTasks.value[idx])
        activeTasks.value.splice(idx, 1)
      }
    } else {
      activeTasks.value.forEach(t => pushToHistory(t))
      activeTasks.value = []
    }
    _syncStorage()
  }

  function pushToHistory(task: TaskState) {
    const existing = taskHistory.value.findIndex(t => t.id === task.id)
    if (existing >= 0) {
      taskHistory.value[existing] = task
    } else {
      taskHistory.value.unshift(task)
      if (taskHistory.value.length > 20) taskHistory.value = taskHistory.value.slice(0, 20)
    }
  }

  return {
    activeTasks,
    currentTask,
    taskHistory,
    isProcessing,
    isTerminal,
    setTask,
    updateProgress,
    clearTask,
    pushToHistory,
  }
})
