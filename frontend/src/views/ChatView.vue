<script setup lang="ts">
import { ref, nextTick, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chat'
import { createChatStream } from '@/api/chat'
import ChatBubble from '@/components/ChatBubble.vue'
import ChatInput from '@/components/ChatInput.vue'

const props = defineProps<{ repoId: string }>()
const chatStore = useChatStore()
const messagesRef = ref<HTMLElement | null>(null)

const suggestions = [
  '这个项目的整体架构是什么？',
  '核心业务逻辑在哪里实现？',
  '有哪些主要的 API 端点？',
  '数据库模型是如何设计的？',
]

let activeEventSource: EventSource | null = null

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

async function handleSend(query: string) {
  if (chatStore.isLoading) return

  // 添加用户消息
  chatStore.addMessage({
    id: crypto.randomUUID(),
    role: 'user',
    content: query,
    timestamp: Date.now(),
  })

  // 添加空 AI 消息（流式填充）
  chatStore.addMessage({
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    timestamp: Date.now(),
    isStreaming: true,
  })

  chatStore.isLoading = true
  scrollToBottom()

  // 关闭上一个连接
  if (activeEventSource) {
    activeEventSource.close()
  }

  activeEventSource = createChatStream({
    repoId: props.repoId,
    sessionId: chatStore.sessionId || undefined,
    query,
    onSessionId: (sid) => {
      chatStore.sessionId = sid
    },
    onToken: (token) => {
      chatStore.appendToLastAssistant(token)
      scrollToBottom()
    },
    onChunkRefs: (refs) => {
      chatStore.setLastAssistantRefs(refs)
    },
    onDone: () => {
      chatStore.finishStreaming()
      activeEventSource = null
    },
    onError: (message) => {
      chatStore.updateLastAssistant(`[错误] ${message}`)
      chatStore.finishStreaming()
      activeEventSource = null
    },
  })
}

function clearChat() {
  chatStore.clearChat()
}

onUnmounted(() => {
  if (activeEventSource) {
    activeEventSource.close()
  }
})
</script>

<template>
  <div class="chat-view">
    <!-- 顶部栏 -->
    <div class="chat-header">
      <div class="chat-header__left">
        <RouterLink :to="{ name: 'wiki', params: { repoId: props.repoId } }" class="back-btn">
          ← 返回 Wiki
        </RouterLink>
        <h2 class="chat-title">AI 代码问答</h2>
      </div>
      <button
        class="btn btn-ghost btn-sm"
        @click="clearChat"
        :disabled="chatStore.messages.length === 0"
      >
        清空对话
      </button>
    </div>

    <!-- 消息区 -->
    <div ref="messagesRef" class="chat-messages">
      <!-- 空状态 -->
      <div v-if="chatStore.messages.length === 0" class="chat-empty">
        <div class="chat-empty__icon">💬</div>
        <h3>开始代码问答</h3>
        <p>基于仓库代码库，你可以提问关于架构、函数、依赖等任何代码相关问题</p>
        <div class="chat-suggestions">
          <button
            v-for="s in suggestions"
            :key="s"
            class="suggestion-chip"
            @click="handleSend(s)"
          >
            {{ s }}
          </button>
        </div>
        <!-- 提示：模块五未实现 -->
        <div class="alert alert-warning" style="margin-top:20px;text-align:left;max-width:480px;">
          ⚠ 注意：AI 问答功能依赖后端模块五（RAG）实现，当前版本后端尚未提供 /api/chat/stream 端点。
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-else class="messages-list">
        <ChatBubble
          v-for="msg in chatStore.messages"
          :key="msg.id"
          :message="msg"
        />
      </div>
    </div>

    <!-- 输入区 -->
    <ChatInput
      :disabled="chatStore.isLoading"
      @send="handleSend"
    />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height));
  flex: 1;
  min-width: 0;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-primary);
  flex-shrink: 0;
}

.chat-header__left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.back-btn {
  font-size: var(--font-size-sm);
  color: var(--text-muted);
  text-decoration: none;
  padding: 4px 8px;
  border-radius: var(--radius);
  transition: all 0.15s;
}
.back-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  text-decoration: none;
}

.chat-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 60px 20px;
  gap: 12px;
}

.chat-empty__icon { font-size: 48px; }

.chat-empty h3 {
  font-size: var(--font-size-xl);
  color: var(--text-primary);
}

.chat-empty p {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  max-width: 400px;
}

.chat-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 8px;
}

.suggestion-chip {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  padding: 6px 14px;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.suggestion-chip:hover {
  background: var(--bg-active);
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.messages-list {
  max-width: 800px;
  margin: 0 auto;
}
</style>
