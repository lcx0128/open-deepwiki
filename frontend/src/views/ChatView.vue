<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '@/stores/chat'
import { createChatStream, createDeepResearchStream } from '@/api/chat'
import { getFileContent } from '@/api/repositories'
import type { ChunkRef } from '@/stores/chat'
import ChatBubble from '@/components/ChatBubble.vue'
import ChatInput from '@/components/ChatInput.vue'
import hljs from 'highlight.js'

const props = defineProps<{ repoId: string }>()
const route = useRoute()
const chatStore = useChatStore()
const messagesRef = ref<HTMLElement | null>(null)

const suggestions = [
  '这个项目的整体架构是什么？',
  '核心业务逻辑在哪里实现？',
  '有哪些主要的 API 端点？',
  '数据库模型是如何设计的？',
]

// Deep Research state
const deepResearch = ref(false)
const drIteration = ref(0)
const drActive = ref(false)
const drQuery = ref('')

// Code panel state
const codePanel = ref<{
  filePath: string
  content: string
  language: string
  startLine: number
  totalLines: number
  isLoading: boolean
  error: string | null
} | null>(null)

// Extracted code blocks state
const extractedCodeBlocks = ref(new Map<string, Array<{id: string; lang: string; content: string}>>())
const highlightedCodeId = ref<string | null>(null)

const panelCodeBlocks = computed(() => {
  const assistantMsgs = chatStore.messages.filter(m => m.role === 'assistant')
  const last = assistantMsgs[assistantMsgs.length - 1]
  if (!last) return []
  return extractedCodeBlocks.value.get(last.id) || []
})

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

function buildMessages(_currentQuery: string): Array<{ role: string; content: string }> {
  return chatStore.messages
    .filter(m => !m.content.startsWith('[继续第'))
    .map(m => ({ role: m.role as string, content: m.content }))
}

async function handleSend(query: string) {
  if (chatStore.isLoading) return
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
    onToken: (token) => { chatStore.appendToLastAssistant(token); scrollToBottom() },
    onChunkRefs: (refs) => { chatStore.setLastAssistantRefs(refs) },
    onDone: () => { chatStore.finishStreaming(); activeEventSource = null },
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

  const messages = buildMessages(query)
  let needsContinue = false

  activeAbortController = createDeepResearchStream({
    repoId: props.repoId,
    sessionId: chatStore.sessionId || undefined,
    query: originalQuery,
    messages,
    onSessionId: (sid) => { chatStore.sessionId = sid },
    onToken: (token) => { chatStore.appendToLastAssistant(token); scrollToBottom() },
    onChunkRefs: (refs) => { chatStore.setLastAssistantRefs(refs) },
    onDeepResearchContinue: (_iteration) => { needsContinue = true },
    onDone: () => {
      chatStore.finishStreaming()
      activeAbortController = null
      if (needsContinue && drIteration.value < 5) {
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
  if (drContinueTimer) clearTimeout(drContinueTimer)
  if (activeAbortController) activeAbortController.abort()
  if (activeEventSource) activeEventSource.close()
  drActive.value = false
  drIteration.value = 0
  drQuery.value = ''
  chatStore.clearChat()
  codePanel.value = null
}

// Load file content into right panel
async function loadFilePanel(ref: ChunkRef) {
  codePanel.value = {
    filePath: ref.file_path,
    content: '',
    language: '',
    startLine: ref.start_line,
    totalLines: 0,
    isLoading: true,
    error: null,
  }
  try {
    const data = await getFileContent(
      props.repoId,
      ref.file_path,
      ref.start_line,
      ref.end_line
    )
    codePanel.value = {
      filePath: data.file_path,
      content: data.content,
      language: data.language,
      startLine: data.start_line,
      totalLines: data.total_lines,
      isLoading: false,
      error: null,
    }
  } catch (err: unknown) {
    if (codePanel.value) {
      codePanel.value.isLoading = false
      codePanel.value.error = '加载文件内容失败'
    }
  }
}

// Syntax-highlighted code for the file panel
const highlightedCode = computed(() => {
  if (!codePanel.value?.content) return ''
  const { content, language } = codePanel.value
  try {
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(content, { language }).value
    }
    return hljs.highlightAuto(content).value
  } catch {
    return content
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  }
})

function handleCodeBlocks(payload: { messageId: string; blocks: Array<{id: string; lang: string; content: string}> }) {
  extractedCodeBlocks.value = new Map(extractedCodeBlocks.value)
  extractedCodeBlocks.value.set(payload.messageId, payload.blocks)
}

function handleCodeBlockFocus(blockId: string) {
  highlightedCodeId.value = blockId
  nextTick(() => {
    const el = document.getElementById(blockId)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

function highlightCode(code: string, lang: string): string {
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return code.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}

onMounted(async () => {
  if (route.query.dr === '1') {
    deepResearch.value = true
  }
  const q = route.query.q as string | undefined
  if (q && q.trim()) {
    await nextTick()
    handleSend(q.trim())
  }
})

onUnmounted(() => {
  if (drContinueTimer) clearTimeout(drContinueTimer)
  if (activeEventSource) activeEventSource.close()
  if (activeAbortController) activeAbortController.abort()
})
</script>

<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-header">
      <div class="chat-header__left">
        <RouterLink :to="{ name: 'wiki', params: { repoId: props.repoId } }" class="back-btn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
          <span>返回 Wiki</span>
        </RouterLink>
        <h1 class="chat-title">AI 代码问答</h1>
      </div>
      <div class="chat-header__right">
        <div v-if="drActive" class="dr-badge">
          <span class="dr-badge__dot" />
          深度研究 {{ drIteration }}/5
        </div>
        <button
          class="btn btn-ghost btn-sm"
          @click="clearChat"
          :disabled="chatStore.messages.length === 0"
        >清空</button>
      </div>
    </div>

    <!-- Main split area -->
    <div class="chat-body">
      <!-- Left: conversation panel -->
      <div class="chat-left">
        <div ref="messagesRef" class="chat-messages">
          <!-- Empty state -->
          <div v-if="chatStore.messages.length === 0" class="chat-empty">
            <div class="chat-empty__icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </div>
            <h3>代码问答</h3>
            <p>基于代码库的语义检索，回答关于架构、函数、依赖的问题</p>
            <div class="chat-suggestions">
              <button
                v-for="s in suggestions"
                :key="s"
                class="suggestion-chip"
                @click="handleSend(s)"
              >{{ s }}</button>
            </div>
          </div>

          <!-- Messages list -->
          <div v-else class="messages-list">
            <ChatBubble
              v-for="msg in chatStore.messages"
              :key="msg.id"
              :message="msg"
              :compact-code="true"
              @ref-click="loadFilePanel"
              @code-blocks="handleCodeBlocks"
              @code-block-focus="handleCodeBlockFocus"
            />
          </div>
        </div>

        <!-- Input -->
        <ChatInput
          :disabled="chatStore.isLoading"
          v-model:deepResearch="deepResearch"
          @send="handleSend"
        />
      </div>

      <!-- Right: code viewer panel -->
      <div class="code-panel">
        <!-- Stacked code blocks from AI response -->
        <div v-if="panelCodeBlocks.length" class="code-blocks-stack">
          <div class="code-blocks-stack__header">
            <span>代码参考</span>
            <span class="code-blocks-stack__count">{{ panelCodeBlocks.length }} 个代码块</span>
          </div>
          <div class="code-blocks-stack__scroll">
            <div
              v-for="block in panelCodeBlocks"
              :key="block.id"
              :id="block.id"
              class="code-block-card"
              :class="{ 'code-block-card--active': highlightedCodeId === block.id }"
            >
              <div class="code-block-card__header">
                <span class="code-block-card__lang">{{ block.lang || 'code' }}</span>
                <span class="code-block-card__lines">{{ block.content.split('\n').length }} 行</span>
              </div>
              <pre class="code-block-card__pre"><code class="hljs" v-html="highlightCode(block.content, block.lang)" /></pre>
            </div>
          </div>
        </div>

        <!-- File panel (when file ref chip was clicked) -->
        <template v-else-if="codePanel">
          <!-- Loading state -->
          <div v-if="codePanel.isLoading" class="code-panel__loading">
            <span class="spinner" />
            <span>加载文件...</span>
          </div>
          <!-- Error state -->
          <div v-else-if="codePanel.error" class="code-panel__error">
            <p>{{ codePanel.error }}</p>
          </div>
          <!-- Code content -->
          <template v-else>
            <div class="code-panel__header">
              <div class="code-panel__filepath">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span>{{ codePanel.filePath }}</span>
              </div>
              <div class="code-panel__meta">
                行 {{ codePanel.startLine }}–{{ codePanel.startLine + codePanel.content.split('\n').length - 1 }}
                <span class="code-panel__lang">{{ codePanel.language }}</span>
              </div>
            </div>
            <div class="code-panel__body">
              <div class="code-panel__lines">
                <span
                  v-for="(_, i) in codePanel.content.split('\n')"
                  :key="i"
                  class="line-num"
                >{{ codePanel.startLine + i }}</span>
              </div>
              <pre class="code-panel__code"><code class="hljs" v-html="highlightedCode" /></pre>
            </div>
          </template>
        </template>

        <!-- Placeholder when nothing selected -->
        <div v-else class="code-panel__empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <polyline points="16 18 22 12 16 6"/>
            <polyline points="8 6 2 12 8 18"/>
          </svg>
          <p>点击回答中的文件引用，在此查看源代码</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height));
  flex: 1;
  min-width: 0;
}

/* Header */
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
  height: 48px;
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
  gap: 10px;
}

.back-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text-muted);
  text-decoration: none;
  padding: 4px 8px;
  border-radius: var(--radius);
  transition: all 0.15s;
}
.back-btn svg { width: 16px; height: 16px; }
.back-btn:hover { background: var(--bg-hover); color: var(--text-primary); text-decoration: none; }

.chat-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.dr-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  background: rgba(124, 58, 237, 0.1);
  border: 1px solid rgba(124, 58, 237, 0.25);
  border-radius: var(--radius-full);
  font-size: 12px;
  color: #7c3aed;
  font-weight: 500;
}

.dr-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #7c3aed;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* Split layout */
.chat-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* Left panel */
.chat-left {
  display: flex;
  flex-direction: column;
  flex: 0 0 58%;
  min-width: 0;
  border-right: 1px solid var(--border-color);
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

.chat-empty__icon {
  width: 52px;
  height: 52px;
  background: var(--bg-secondary);
  border-radius: var(--radius-xl);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}

.chat-empty__icon svg { width: 26px; height: 26px; }

.chat-empty h3 {
  font-size: var(--font-size-xl);
  font-weight: 600;
  color: var(--text-primary);
}

.chat-empty p {
  font-size: 13px;
  color: var(--text-tertiary);
  max-width: 380px;
  line-height: 1.6;
}

.chat-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 4px;
}

.suggestion-chip {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  padding: 6px 14px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}

.suggestion-chip:hover {
  background: var(--bg-active);
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.messages-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Right: code panel */
.code-panel {
  flex: 0 0 42%;
  display: flex;
  flex-direction: column;
  background: #0d0d17;
  overflow: hidden;
}

.code-panel__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  height: 100%;
  color: rgba(255, 255, 255, 0.25);
  text-align: center;
  padding: 40px;
}

.code-panel__empty svg {
  width: 40px;
  height: 40px;
  opacity: 0.3;
}

.code-panel__empty p {
  font-size: 13px;
  max-width: 260px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.45);
}

.code-panel__loading {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
  height: 100%;
  color: #5a5a8a;
  font-size: 13px;
}

.code-panel__error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #ef4444;
  font-size: 13px;
}

.code-panel__header {
  padding: 10px 16px;
  background: #111120;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}

.code-panel__filepath {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: #94a3b8;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.code-panel__filepath svg { width: 13px; height: 13px; flex-shrink: 0; color: #60a5fa; }

.code-panel__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #475569;
  flex-shrink: 0;
}

.code-panel__lang {
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 6px;
  border-radius: 3px;
  font-family: var(--font-mono);
  text-transform: uppercase;
  font-size: 10px;
  color: #64748b;
  letter-spacing: 0.04em;
}

.code-panel__body {
  flex: 1;
  overflow: auto;
  display: flex;
}

.code-panel__lines {
  padding: 1em 0;
  min-width: 44px;
  background: #0a0a14;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex-shrink: 0;
  user-select: none;
}

.line-num {
  display: block;
  padding: 0 10px 0 6px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: #2e2e4a;
  line-height: 1.65;
  text-align: right;
}

.code-panel__code {
  flex: 1;
  margin: 0;
  padding: 1em 1.25em;
  background: #0d0d17;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre;
}

.code-panel__code :deep(.hljs) {
  background: transparent;
  color: #e2e8f0;
}

/* Code blocks stack (extracted from AI response) */
.code-blocks-stack {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.code-blocks-stack__header {
  padding: 10px 16px;
  background: #111120;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #94a3b8;
  flex-shrink: 0;
}

.code-blocks-stack__count {
  font-size: 11px;
  color: #475569;
}

.code-blocks-stack__scroll {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: #0a0a14;
}

.code-block-card {
  background: #0d0d17;
  border-left: 3px solid transparent;
  transition: border-color 0.2s;
}

.code-block-card--active {
  border-left-color: var(--color-primary);
  box-shadow: inset 3px 0 0 var(--color-primary);
}

.code-block-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 14px;
  background: #111120;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.code-block-card__lang {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #60a5fa;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 600;
}

.code-block-card__lines {
  font-size: 11px;
  color: #475569;
}

.code-block-card__pre {
  margin: 0;
  padding: 12px 16px;
  background: #0d0d17;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}

@media (max-width: 1024px) {
  .chat-left { flex: 0 0 100%; }
  .code-panel { display: none; }
}
</style>
