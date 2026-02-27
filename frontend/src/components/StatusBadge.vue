<script setup lang="ts">
import { computed } from 'vue'

type StatusType = 'pending' | 'cloning' | 'parsing' | 'embedding' | 'generating' | 'completed' | 'ready' | 'error' | 'failed' | 'syncing' | 'cancelled' | 'interrupted'

const props = defineProps<{
  status: StatusType
  size?: 'sm' | 'md'
}>()

const config: Record<StatusType, { label: string; color: string; bg: string; dot?: boolean }> = {
  pending: { label: '等待中', color: '#92400e', bg: '#fef3c7', dot: true },
  cloning: { label: '克隆中', color: '#1d4ed8', bg: '#dbeafe', dot: true },
  parsing: { label: '解析中', color: '#1d4ed8', bg: '#dbeafe', dot: true },
  embedding: { label: '向量化', color: '#6d28d9', bg: '#ede9fe', dot: true },
  generating: { label: '生成中', color: '#0e7490', bg: '#cffafe', dot: true },
  completed: { label: '完成', color: '#065f46', bg: '#d1fae5' },
  ready: { label: '就绪', color: '#065f46', bg: '#d1fae5' },
  error: { label: '失败', color: '#991b1b', bg: '#fee2e2' },
  failed: { label: '失败', color: '#991b1b', bg: '#fee2e2' },
  syncing: { label: '同步中', color: '#0e7490', bg: '#cffafe', dot: true },
  cancelled: { label: '已取消', color: '#6b7280', bg: '#f3f4f6' },
  interrupted: { label: '已中断', color: '#92400e', bg: '#fef3c7' },
}

const c = computed(() => config[props.status] || config.pending)
</script>

<template>
  <span
    class="badge"
    :class="size === 'sm' ? 'badge--sm' : 'badge--md'"
    :style="{ color: c.color, background: c.bg }"
  >
    <span v-if="c.dot" class="badge__dot" :style="{ background: c.color }" />
    {{ c.label }}
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: var(--radius-full);
  font-weight: 500;
  white-space: nowrap;
}

.badge--md {
  padding: 3px 10px;
  font-size: var(--font-size-xs);
}

.badge--sm {
  padding: 2px 7px;
  font-size: 11px;
}

.badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
