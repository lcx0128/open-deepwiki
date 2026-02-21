import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ChunkRef {
  filePath: string
  startLine: number
  endLine: number
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
      last.chunkRefs = refs
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
