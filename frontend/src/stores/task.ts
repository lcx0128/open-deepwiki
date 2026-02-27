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

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskState | null>(null)
  const taskHistory = ref<TaskState[]>([])

  const isProcessing = computed(() =>
    currentTask.value !== null &&
    !['completed', 'failed', 'cancelled', 'interrupted'].includes(currentTask.value.status)
  )

  const isTerminal = computed(() =>
    currentTask.value !== null &&
    ['completed', 'failed', 'cancelled', 'interrupted'].includes(currentTask.value.status)
  )

  function setTask(task: TaskState) {
    currentTask.value = task
    localStorage.setItem('activeTaskId', task.id)
  }

  function updateProgress(data: Partial<TaskState>) {
    if (currentTask.value) {
      Object.assign(currentTask.value, data)
    }
  }

  function clearTask() {
    currentTask.value = null
    localStorage.removeItem('activeTaskId')
  }

  function pushToHistory(task: TaskState) {
    const existing = taskHistory.value.findIndex(t => t.id === task.id)
    if (existing >= 0) {
      taskHistory.value[existing] = task
    } else {
      taskHistory.value.unshift(task)
      if (taskHistory.value.length > 20) {
        taskHistory.value = taskHistory.value.slice(0, 20)
      }
    }
  }

  return {
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
