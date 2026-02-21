<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  send: [message: string]
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
</script>

<template>
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
      class="chat-input__btn btn btn-primary"
      :disabled="disabled || !inputText.trim()"
      @click="handleSend"
    >
      <span v-if="disabled">
        <span class="spinner" style="width:16px;height:16px;border-width:2px;" />
      </span>
      <span v-else>发送</span>
    </button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  padding: 12px 16px;
  background: var(--bg-primary);
  border-top: 1px solid var(--border-color);
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
  min-width: 72px;
}
</style>
