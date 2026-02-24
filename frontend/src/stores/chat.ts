import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ChunkRef {
  file_path: string
  start_line: number
  end_line: number
  name: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  chunkRefs?: ChunkRef[]
  isStreaming?: boolean
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const sessionId = ref<string | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function updateLastAssistant(content: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content = content
    }
  }

  function appendToLastAssistant(token: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content += token
    }
  }

  function setLastAssistantRefs(refs: ChunkRef[]) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      const seen = new Set<string>()
      last.chunkRefs = refs.filter(r => {
        const key = `${r.file_path}:${r.start_line}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
    }
  }

  function finishStreaming() {
    const last = messages.value[messages.value.length - 1]
    if (last) {
      last.isStreaming = false
    }
    isLoading.value = false
  }

  function clearChat() {
    messages.value = []
    sessionId.value = null
    isLoading.value = false
    error.value = null
  }

  return {
    messages,
    sessionId,
    isLoading,
    error,
    addMessage,
    updateLastAssistant,
    appendToLastAssistant,
    setLastAssistantRefs,
    finishStreaming,
    clearChat,
  }
})
