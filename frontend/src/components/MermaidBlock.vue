<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue'
import { useMermaid } from '@/composables/useMermaid'

const props = defineProps<{ code: string; id: string }>()

const containerRef = ref<HTMLElement | null>(null)
const renderError = ref<string | null>(null)
const isRendering = ref(false)
const { renderDiagram } = useMermaid()

async function render() {
  if (!props.code?.trim()) return
  isRendering.value = true
  renderError.value = null

  const { svg, error } = await renderDiagram(props.id, props.code)

  if (error) {
    // 先设 error 再关 isRendering，两次赋值在同一批次合并，直接从 loading 跳到 fallback
    renderError.value = error
    isRendering.value = false
  } else {
    isRendering.value = false
    // 必须等 DOM 更新后 containerRef div 才会挂载，否则 containerRef.value 为 null
    await nextTick()
    if (containerRef.value) {
      containerRef.value.innerHTML = svg
    }
  }
}

onMounted(() => nextTick(render))
watch(() => props.code, () => nextTick(render))
</script>

<template>
  <div class="mermaid-wrap">
    <!-- 渲染中 -->
    <div v-if="isRendering" class="mermaid-loading">
      <span class="spinner" />
      <span>渲染图表中...</span>
    </div>

    <!-- 成功渲染的 SVG -->
    <div v-else-if="!renderError" ref="containerRef" class="mermaid-svg" />

    <!-- 降级：原始代码块 -->
    <div v-else class="mermaid-fallback">
      <div class="mermaid-fallback__header">
        <span>⚠ Mermaid 渲染失败，显示原始代码</span>
        <code class="mermaid-fallback__error">{{ renderError }}</code>
      </div>
      <pre class="mermaid-fallback__code"><code>{{ code }}</code></pre>
    </div>
  </div>
</template>

<style scoped>
.mermaid-wrap { margin: 16px 0; }

.mermaid-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
}

.mermaid-svg {
  text-align: center;
  overflow-x: auto;
}

.mermaid-svg :deep(svg) {
  max-width: 100%;
  height: auto;
}

.mermaid-fallback {
  border: 1px solid #fbbf24;
  border-radius: var(--radius);
  overflow: hidden;
}

.mermaid-fallback__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fef3c7;
  padding: 8px 12px;
  font-size: var(--font-size-xs);
  color: #92400e;
  flex-wrap: wrap;
  gap: 4px;
}

.mermaid-fallback__error {
  background: transparent;
  color: #b45309;
  font-size: 11px;
  font-family: var(--font-mono);
}

.mermaid-fallback__code {
  padding: 12px;
  background: #1f2937;
  color: #e5e7eb;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  margin: 0;
  line-height: 1.6;
}
</style>
