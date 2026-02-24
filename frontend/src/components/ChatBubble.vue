<script setup lang="ts">
import MarkdownView from './MarkdownView.vue'
import type { ChatMessage, ChunkRef } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage; compactCode?: boolean }>()

const emit = defineEmits<{
  'ref-click': [ref: ChunkRef]
  'code-blocks': [payload: { messageId: string; blocks: Array<{id: string; lang: string; content: string}> }]
  'code-block-focus': [blockId: string]
}>()

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="bubble-wrap" :class="`bubble-wrap--${message.role}`">
    <!-- Avatar -->
    <div class="bubble__avatar" :class="`bubble__avatar--${message.role}`">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>

    <!-- Content -->
    <div class="bubble__body">
      <!-- Message content -->
      <div class="bubble__content" :class="`bubble__content--${message.role}`">
        <!-- AI answers use Markdown rendering -->
        <MarkdownView
          v-if="message.role === 'assistant'"
          :content="message.content"
          :compact-code="compactCode"
          @code-blocks-extracted="blocks => emit('code-blocks', { messageId: message.id, blocks })"
          @code-block-click="id => emit('code-block-focus', id)"
        />
        <p v-else class="user-text">{{ message.content }}</p>

        <!-- Streaming cursor -->
        <span v-if="message.isStreaming" class="typing-cursor" />
      </div>

      <!-- Code refs -->
      <div v-if="message.chunkRefs?.length" class="bubble__refs">
        <span class="refs__label">参考代码:</span>
        <a
          v-for="(ref, i) in message.chunkRefs"
          :key="i"
          class="ref-chip"
          :title="`${ref.file_path} 第 ${ref.start_line}-${ref.end_line} 行`"
          @click="emit('ref-click', ref)"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="ref-chip__icon">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
          </svg>
          {{ ref.file_path.split('/').pop() }}:{{ ref.start_line }}-{{ ref.end_line }}
        </a>
      </div>

      <!-- Timestamp -->
      <div class="bubble__time">{{ formatTime(message.timestamp) }}</div>
    </div>
  </div>
</template>

<style scoped>
.bubble-wrap {
  display: flex;
  gap: 12px;
  padding: 8px 0;
}

.bubble-wrap--user {
  flex-direction: row-reverse;
}

.bubble__avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  background: var(--bg-tertiary);
}

.bubble__body {
  max-width: 75%;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bubble-wrap--user .bubble__body {
  align-items: flex-end;
}

.bubble__content {
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  font-size: var(--font-size-sm);
  line-height: 1.6;
}

.bubble__content--user {
  background: var(--color-primary);
  color: white;
  border-bottom-right-radius: 4px;
}

.bubble__content--assistant {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-bottom-left-radius: 4px;
  color: var(--text-primary);
}

.user-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble__refs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 4px 0;
}

.refs__label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  white-space: nowrap;
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  padding: 2px 8px;
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
  text-decoration: none;
}

.ref-chip:hover {
  background: var(--bg-active);
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.ref-chip__icon {
  width: 11px;
  height: 11px;
  flex-shrink: 0;
}

.bubble__time {
  font-size: 11px;
  color: var(--text-muted);
  padding: 0 4px;
}
</style>
