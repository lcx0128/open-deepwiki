import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useTaskStore } from '@/stores/task'
import type { TaskState } from '@/stores/task'

const mockTask: TaskState = {
  id: 'task-001',
  repoId: 'repo-001',
  type: 'full_process',
  status: 'cloning',
  progressPct: 10,
  currentStage: '正在克隆仓库...',
  filesTotal: 0,
  filesProcessed: 0,
  errorMsg: null,
  wikiId: null,
}

describe('useTaskStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('初始状态为空', () => {
    const store = useTaskStore()
    expect(store.currentTask).toBeNull()
    expect(store.taskHistory).toHaveLength(0)
    expect(store.isProcessing).toBe(false)
    expect(store.isTerminal).toBe(false)
  })

  it('setTask 设置当前任务', () => {
    const store = useTaskStore()
    store.setTask(mockTask)
    expect(store.currentTask).toEqual(mockTask)
    expect(store.currentTask?.id).toBe('task-001')
  })

  it('isProcessing 在处理状态时为 true', () => {
    const store = useTaskStore()
    store.setTask({ ...mockTask, status: 'cloning' })
    expect(store.isProcessing).toBe(true)

    store.setTask({ ...mockTask, status: 'parsing' })
    expect(store.isProcessing).toBe(true)

    store.setTask({ ...mockTask, status: 'embedding' })
    expect(store.isProcessing).toBe(true)

    store.setTask({ ...mockTask, status: 'generating' })
    expect(store.isProcessing).toBe(true)
  })

  it('isProcessing 在终止状态时为 false', () => {
    const store = useTaskStore()
    store.setTask({ ...mockTask, status: 'completed' })
    expect(store.isProcessing).toBe(false)

    store.setTask({ ...mockTask, status: 'failed' })
    expect(store.isProcessing).toBe(false)

    store.setTask({ ...mockTask, status: 'cancelled' })
    expect(store.isProcessing).toBe(false)
  })

  it('isTerminal 在终止状态时为 true', () => {
    const store = useTaskStore()
    store.setTask({ ...mockTask, status: 'completed' })
    expect(store.isTerminal).toBe(true)

    store.setTask({ ...mockTask, status: 'failed' })
    expect(store.isTerminal).toBe(true)
  })

  it('updateProgress 更新当前任务字段', () => {
    const store = useTaskStore()
    store.setTask(mockTask)
    store.updateProgress({ progressPct: 45, currentStage: '解析中...', filesProcessed: 10 })
    expect(store.currentTask?.progressPct).toBe(45)
    expect(store.currentTask?.currentStage).toBe('解析中...')
    expect(store.currentTask?.filesProcessed).toBe(10)
    // other fields unchanged
    expect(store.currentTask?.id).toBe('task-001')
  })

  it('updateProgress 在无任务时不报错', () => {
    const store = useTaskStore()
    expect(() => store.updateProgress({ progressPct: 50 })).not.toThrow()
  })

  it('clearTask 清空当前任务', () => {
    const store = useTaskStore()
    store.setTask(mockTask)
    store.clearTask()
    expect(store.currentTask).toBeNull()
    expect(store.isProcessing).toBe(false)
  })

  it('pushToHistory 添加到历史', () => {
    const store = useTaskStore()
    store.pushToHistory(mockTask)
    expect(store.taskHistory).toHaveLength(1)
    expect(store.taskHistory[0].id).toBe('task-001')
  })

  it('pushToHistory 更新已存在的历史记录', () => {
    const store = useTaskStore()
    store.pushToHistory(mockTask)
    store.pushToHistory({ ...mockTask, status: 'completed', progressPct: 100 })
    expect(store.taskHistory).toHaveLength(1)
    expect(store.taskHistory[0].status).toBe('completed')
  })

  it('pushToHistory 最多保留 20 条', () => {
    const store = useTaskStore()
    for (let i = 0; i < 25; i++) {
      store.pushToHistory({ ...mockTask, id: `task-${i}` })
    }
    expect(store.taskHistory.length).toBeLessThanOrEqual(20)
  })

  it('pushToHistory 新任务插入到最前', () => {
    const store = useTaskStore()
    store.pushToHistory({ ...mockTask, id: 'old' })
    store.pushToHistory({ ...mockTask, id: 'new' })
    expect(store.taskHistory[0].id).toBe('new')
  })
})
