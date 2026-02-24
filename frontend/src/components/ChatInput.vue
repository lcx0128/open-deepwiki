<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
  deepResearch?: boolean
}>()

const emit = defineEmits<{
  send: [message: string]
  'update:deepResearch': [value: boolean]
}>()

const inputText = ref('')

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function toggleDeepResearch() {
  emit('update:deepResearch', !props.deepResearch)
}
</script>

<template>
  <div class="chat-input-wrapper">
    <!-- Deep Research 开关栏 -->
    <div class="deep-research-bar">
      <button
        class="deep-research-toggle"
        :class="{ active: deepResearch }"
        @click="toggleDeepResearch"
        :title="deepResearch ? '关闭深度研究模式' : '开启深度研究模式（5轮迭代）'"
      >
        <span class="toggle-icon">{{ deepResearch ? '🔬' : '🔍' }}</span>
        <span class="toggle-label">深度研究</span>
        <span class="toggle-status">{{ deepResearch ? '已开启' : '关闭' }}</span>
      </button>
      <span v-if="deepResearch" class="deep-research-hint">
        AI 将进行最多 5 轮深度分析，逐步综合得出完整结论
      </span>
    </div>

    <!-- 输入区 -->
    <div class="chat-input">
      <textarea
        v-model="inputText"
        class="chat-input__textarea"
        :placeholder="placeholder || '输入你关于代码的问题... (Enter 发送，Shift+Enter 换行)'"
        :disabled="disabled"
        @keydown="handleKeydown"
        rows="2"
      />
      <button
        class="chat-input__btn btn"
        :class="deepResearch ? 'btn-deep-research' : 'btn-primary'"
        :disabled="disabled || !inputText.trim()"
        @click="handleSend"
      >
        <span v-if="disabled">
          <span class="spinner" style="width:16px;height:16px;border-width:2px;" />
        </span>
        <span v-else>{{ deepResearch ? '深度研究' : '发送' }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-input-wrapper {
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  border-top: 1px solid var(--border-color);
}

.deep-research-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px 4px;
  border-bottom: 1px solid transparent;
  transition: border-color 0.2s;
}

.deep-research-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  cursor: pointer;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  transition: all 0.15s;
}

.deep-research-toggle:hover {
  border-color: #7c3aed;
  color: #7c3aed;
}

.deep-research-toggle.active {
  background: #7c3aed;
  border-color: #7c3aed;
  color: white;
}

.toggle-icon { font-size: 14px; }
.toggle-label { font-weight: 500; }
.toggle-status {
  font-size: 11px;
  opacity: 0.8;
}

.deep-research-hint {
  font-size: 11px;
  color: #7c3aed;
  opacity: 0.85;
}

.chat-input {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 16px 12px;
}

.chat-input__textarea {
  flex: 1;
  resize: none;
  padding: 10px 14px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s;
  line-height: 1.5;
}

.chat-input__textarea:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}

.chat-input__textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.chat-input__btn {
  flex-shrink: 0;
  height: 40px;
  min-width: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.btn-deep-research {
  background: #7c3aed;
  color: white;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  font-weight: 500;
  transition: background 0.15s;
}

.btn-deep-research:hover:not(:disabled) {
  background: #6d28d9;
}

.btn-deep-research:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
