<script setup lang="ts">
import { ref, nextTick, onUnmounted } from 'vue'
import { useChatStore } from '@/stores/chat'
import { createChatStream, createDeepResearchStream } from '@/api/chat'
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

// Deep Research 状态
const deepResearch = ref(false)
const drIteration = ref(0)       // 当前轮次（1-5）
const drActive = ref(false)      // 是否正在进行 Deep Research
const drQuery = ref('')          // 原始问题（整个研究过程保持不变）

let activeEventSource: EventSource | null = null
let activeAbortController: AbortController | null = null
let drContinueTimer: ReturnType<typeof setTimeout> | null = null

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

/** 构建当前对话历史，用于 Deep Research messages 参数（过滤掉自动插入的续研消息） */
function buildMessages(_currentQuery: string): Array<{ role: string; content: string }> {
  const history = chatStore.messages
    .filter(m => !m.content.startsWith('[继续第'))
    .map(m => ({
      role: m.role as string,
      content: m.content,
    }))
  // 最后加上当前问题（如果尚未在 store 中）
  return history
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
  scrollToBottom()

  if (deepResearch.value) {
    await startDeepResearch(query, query)
  } else {
    await startNormalChat(query)
  }
}

async function startNormalChat(query: string) {
  // 添加空 AI 消息（流式填充）
  chatStore.addMessage({
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    timestamp: Date.now(),
    isStreaming: true,
  })
  chatStore.isLoading = true

  if (activeEventSource) activeEventSource.close()

  activeEventSource = createChatStream({
    repoId: props.repoId,
    sessionId: chatStore.sessionId || undefined,
    query,
    onSessionId: (sid) => { chatStore.sessionId = sid },
    onToken: (token) => {
      chatStore.appendToLastAssistant(token)
      scrollToBottom()
    },
    onChunkRefs: (refs) => { chatStore.setLastAssistantRefs(refs) },
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

async function startDeepResearch(query: string, originalQuery: string) {
  drActive.value = true
  drQuery.value = originalQuery
  drIteration.value++

  // 添加空 AI 消息（带迭代标记）
  chatStore.addMessage({
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    timestamp: Date.now(),
    isStreaming: true,
  })
  chatStore.isLoading = true
  scrollToBottom()

  if (activeAbortController) activeAbortController.abort()

  // 构建消息历史（包含当前已添加的 user 消息）
  const messages = buildMessages(query)

  let needsContinue = false

  activeAbortController = createDeepResearchStream({
    repoId: props.repoId,
    sessionId: chatStore.sessionId || undefined,
    query: originalQuery,
    messages,
    onSessionId: (sid) => { chatStore.sessionId = sid },
    onToken: (token) => {
      chatStore.appendToLastAssistant(token)
      scrollToBottom()
    },
    onChunkRefs: (refs) => { chatStore.setLastAssistantRefs(refs) },
    onDeepResearchContinue: (_iteration) => {
      needsContinue = true
    },
    onDone: () => {
      chatStore.finishStreaming()
      activeAbortController = null

      if (needsContinue && drIteration.value < 5) {
        // 等 1.5 秒后自动发起下一轮
        drContinueTimer = setTimeout(() => {
          chatStore.addMessage({
            id: crypto.randomUUID(),
            role: 'user',
            content: `[继续第 ${drIteration.value + 1} 轮深度研究...]`,
            timestamp: Date.now(),
          })
          startDeepResearch(originalQuery, originalQuery)
        }, 1500)
      } else {
        // 研究完成
        drActive.value = false
        drIteration.value = 0
        drQuery.value = ''
      }
    },
    onError: (message) => {
      chatStore.updateLastAssistant(`[深度研究错误] ${message}`)
      chatStore.finishStreaming()
      activeAbortController = null
      drActive.value = false
      drIteration.value = 0
    },
  })
}

function clearChat() {
  // 停止进行中的研究
  if (drContinueTimer) clearTimeout(drContinueTimer)
  if (activeAbortController) activeAbortController.abort()
  if (activeEventSource) activeEventSource.close()
  drActive.value = false
  drIteration.value = 0
  drQuery.value = ''
  chatStore.clearChat()
}

onUnmounted(() => {
  if (drContinueTimer) clearTimeout(drContinueTimer)
  if (activeEventSource) activeEventSource.close()
  if (activeAbortController) activeAbortController.abort()
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
      <div class="chat-header__right">
        <!-- Deep Research 进度指示 -->
        <div v-if="drActive" class="dr-progress">
          <span class="dr-progress__icon">🔬</span>
          <span class="dr-progress__text">
            深度研究 第 {{ drIteration }} / 5 轮
          </span>
          <span class="dr-progress__dots">
            <span v-for="i in 5" :key="i" class="dot" :class="{ active: i <= drIteration, current: i === drIteration }" />
          </span>
        </div>
        <button
          class="btn btn-ghost btn-sm"
          @click="clearChat"
          :disabled="chatStore.messages.length === 0"
        >
          清空对话
        </button>
      </div>
    </div>

    <!-- 消息区 -->
    <div ref="messagesRef" class="chat-messages">
      <!-- 空状态 -->
      <div v-if="chatStore.messages.length === 0" class="chat-empty">
        <div class="chat-empty__icon">💬</div>
        <h3>开始代码问答</h3>
        <p>基于仓库代码库，你可以提问关于架构、函数、依赖等任何代码相关问题</p>
        <p v-if="deepResearch" class="dr-empty-hint">
          🔬 深度研究模式已开启 — AI 将进行 5 轮迭代，逐步深入分析给出综合结论
        </p>
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
      v-model:deepResearch="deepResearch"
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

.chat-header__right {
  display: flex;
  align-items: center;
  gap: 12px;
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

/* Deep Research 进度 */
.dr-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  background: #f3e8ff;
  border-radius: var(--radius-full);
  border: 1px solid #c4b5fd;
}

.dr-progress__icon { font-size: 14px; }

.dr-progress__text {
  font-size: var(--font-size-sm);
  color: #7c3aed;
  font-weight: 500;
}

.dr-progress__dots {
  display: flex;
  gap: 4px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ddd;
  transition: all 0.3s;
}

.dot.active { background: #c4b5fd; }
.dot.current {
  background: #7c3aed;
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.3);
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

.dr-empty-hint {
  color: #7c3aed !important;
  font-weight: 500;
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
