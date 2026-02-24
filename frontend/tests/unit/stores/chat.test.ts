import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import type { ChatMessage, ChunkRef } from '@/stores/chat'

function makeMsg(role: 'user' | 'assistant', content = 'hello'): ChatMessage {
  return {
    id: `msg-${Date.now()}-${Math.random()}`,
    role,
    content,
    timestamp: Date.now(),
  }
}

describe('useChatStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('初始状态为空', () => {
    const store = useChatStore()
    expect(store.messages).toHaveLength(0)
    expect(store.sessionId).toBeNull()
    expect(store.isLoading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('addMessage 添加消息', () => {
    const store = useChatStore()
    const msg = makeMsg('user', '你好')
    store.addMessage(msg)
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].content).toBe('你好')
    expect(store.messages[0].role).toBe('user')
  })

  it('addMessage 保持插入顺序', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('user', 'q1'))
    store.addMessage(makeMsg('assistant', 'a1'))
    store.addMessage(makeMsg('user', 'q2'))
    expect(store.messages[0].content).toBe('q1')
    expect(store.messages[1].content).toBe('a1')
    expect(store.messages[2].content).toBe('q2')
  })

  it('updateLastAssistant 替换最后一条 assistant 消息内容', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('assistant', '初始内容'))
    store.updateLastAssistant('更新后内容')
    expect(store.messages[0].content).toBe('更新后内容')
  })

  it('updateLastAssistant 不修改 user 消息', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('user', '用户消息'))
    store.updateLastAssistant('尝试修改')
    expect(store.messages[0].content).toBe('用户消息')
  })

  it('appendToLastAssistant 追加 token', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('assistant', '你好'))
    store.appendToLastAssistant('，世界')
    expect(store.messages[0].content).toBe('你好，世界')
  })

  it('appendToLastAssistant 多次追加累积内容', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('assistant', ''))
    store.appendToLastAssistant('Hello')
    store.appendToLastAssistant(' ')
    store.appendToLastAssistant('World')
    expect(store.messages[0].content).toBe('Hello World')
  })

  it('setLastAssistantRefs 设置引用', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('assistant', '答复'))
    const refs: ChunkRef[] = [{ filePath: 'src/app.py', startLine: 1, endLine: 10 }]
    store.setLastAssistantRefs(refs)
    expect(store.messages[0].chunkRefs).toEqual(refs)
  })

  it('finishStreaming 将最后消息 isStreaming 设为 false', () => {
    const store = useChatStore()
    store.addMessage({ ...makeMsg('assistant', '...'), isStreaming: true })
    store.isLoading = true
    store.finishStreaming()
    expect(store.messages[0].isStreaming).toBe(false)
    expect(store.isLoading).toBe(false)
  })

  it('clearChat 清空所有状态', () => {
    const store = useChatStore()
    store.addMessage(makeMsg('user', '测试'))
    store.sessionId = 'sess-001'
    store.isLoading = true
    store.error = '错误'
    store.clearChat()
    expect(store.messages).toHaveLength(0)
    expect(store.sessionId).toBeNull()
    expect(store.isLoading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('appendToLastAssistant 在无消息时不报错', () => {
    const store = useChatStore()
    expect(() => store.appendToLastAssistant('token')).not.toThrow()
  })

  it('setLastAssistantRefs 在无消息时不报错', () => {
    const store = useChatStore()
    expect(() => store.setLastAssistantRefs([])).not.toThrow()
  })
})
