<script setup lang="ts">
import MarkdownView from './MarkdownView.vue'
import type { ChatMessage } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage }>()

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="bubble-wrap" :class="`bubble-wrap--${message.role}`">
    <!-- 头像 -->
    <div class="bubble__avatar" :class="`bubble__avatar--${message.role}`">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>

    <!-- 内容 -->
    <div class="bubble__body">
      <!-- 消息内容 -->
      <div class="bubble__content" :class="`bubble__content--${message.role}`">
        <!-- AI 回答用 Markdown 渲染 -->
        <MarkdownView v-if="message.role === 'assistant'" :content="message.content" />
        <p v-else class="user-text">{{ message.content }}</p>

        <!-- 流式光标 -->
        <span v-if="message.isStreaming" class="typing-cursor" />
      </div>

      <!-- 代码引用 -->
      <div v-if="message.chunkRefs?.length" class="bubble__refs">
        <span class="refs__label">参考代码:</span>
        <a
          v-for="(ref, i) in message.chunkRefs"
          :key="i"
          class="ref-chip"
          :title="`${ref.filePath} 第 ${ref.startLine}-${ref.endLine} 行`"
        >
          {{ ref.filePath.split('/').pop() }}:{{ ref.startLine }}-{{ ref.endLine }}
        </a>
      </div>

      <!-- 时间戳 -->
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
  display: inline-block;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  padding: 2px 8px;
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  cursor: default;
  white-space: nowrap;
}

.bubble__time {
  font-size: 11px;
  color: var(--text-muted);
  padding: 0 4px;
}
</style>
