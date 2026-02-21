<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: string
  progressPct: number
  currentStage: string
  filesProcessed?: number | null
  filesTotal?: number | null
  errorMsg?: string | null
}>()

const stages = [
  { key: 'cloning', label: '克隆', fullLabel: '克隆仓库' },
  { key: 'parsing', label: '解析', fullLabel: '解析代码' },
  { key: 'embedding', label: '向量化', fullLabel: '生成向量' },
  { key: 'generating', label: '生成', fullLabel: '生成 Wiki' },
  { key: 'completed', label: '完成', fullLabel: '处理完成' },
]

const currentStageIndex = computed(() => {
  if (props.status === 'pending') return -1
  const idx = stages.findIndex(s => s.key === props.status)
  if (idx >= 0) return idx
  // failed/cancelled: find stage by currentStage text
  return stages.findIndex(s => s.key === props.currentStage)
})

const barColor = computed(() => {
  if (props.status === 'completed') return '#10b981'
  if (props.status === 'failed') return '#ef4444'
  return '#2563eb'
})

const safeProgress = computed(() => Math.max(0, Math.min(100, props.progressPct || 0)))
</script>

<template>
  <div class="progress-wrap">
    <!-- 阶段步骤指示器 -->
    <div class="stages">
      <div
        v-for="(stage, index) in stages"
        :key="stage.key"
        class="stage"
        :class="{
          'stage--active': index === currentStageIndex && status !== 'failed',
          'stage--done': index < currentStageIndex || status === 'completed',
          'stage--failed': status === 'failed' && index === currentStageIndex,
        }"
      >
        <div class="stage__dot">
          <span v-if="index < currentStageIndex || status === 'completed'">✓</span>
          <span v-else-if="status === 'failed' && index === currentStageIndex">✗</span>
          <span v-else>{{ index + 1 }}</span>
        </div>
        <span class="stage__label">{{ stage.fullLabel }}</span>
        <div v-if="index < stages.length - 1" class="stage__connector"
          :class="{ 'stage__connector--done': index < currentStageIndex }" />
      </div>
    </div>

    <!-- 进度条 -->
    <div class="bar-track">
      <div
        class="bar-fill"
        :style="{ width: safeProgress + '%', background: barColor }"
      />
    </div>

    <!-- 状态文本 -->
    <div class="status-row">
      <span class="status-stage">
        <span v-if="status === 'failed'" class="status--failed">处理失败</span>
        <span v-else>{{ currentStage || '等待中...' }}</span>
      </span>
      <div class="status-right">
        <span v-if="filesTotal && filesTotal > 0" class="status-files">
          {{ filesProcessed || 0 }}/{{ filesTotal }} 文件
        </span>
        <span class="status-pct" :style="{ color: barColor }">{{ Math.round(safeProgress) }}%</span>
      </div>
    </div>

    <!-- 错误信息 -->
    <div v-if="status === 'failed' && errorMsg" class="error-msg">
      <span>错误：{{ errorMsg }}</span>
    </div>
  </div>
</template>

<style scoped>
.progress-wrap {
  padding: 20px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.stages {
  display: flex;
  align-items: flex-start;
  margin-bottom: 20px;
  position: relative;
}

.stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  position: relative;
  opacity: 0.4;
  transition: opacity 0.3s;
}

.stage--active { opacity: 1; }
.stage--done { opacity: 0.9; }
.stage--failed { opacity: 1; }

.stage__dot {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg-tertiary);
  border: 2px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 6px;
  transition: all 0.3s;
  z-index: 1;
}

.stage--active .stage__dot {
  background: #2563eb;
  border-color: #2563eb;
  color: white;
  box-shadow: 0 0 0 4px #dbeafe;
}

.stage--done .stage__dot {
  background: #10b981;
  border-color: #10b981;
  color: white;
}

.stage--failed .stage__dot {
  background: #ef4444;
  border-color: #ef4444;
  color: white;
}

.stage__label {
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
  white-space: nowrap;
}

.stage--active .stage__label,
.stage--done .stage__label {
  color: var(--text-secondary);
}

.stage__connector {
  position: absolute;
  top: 15px;
  left: 50%;
  width: 100%;
  height: 2px;
  background: var(--border-color);
  z-index: 0;
  transition: background 0.3s;
}

.stage__connector--done {
  background: #10b981;
}

.bar-track {
  width: 100%;
  height: 6px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  overflow: hidden;
  margin-bottom: 12px;
}

.bar-fill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width 0.6s ease, background 0.3s;
}

.status-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--font-size-sm);
}

.status-stage { color: var(--text-secondary); }
.status--failed { color: #ef4444; font-weight: 500; }
.status-right { display: flex; align-items: center; gap: 12px; }
.status-files { color: var(--text-muted); }
.status-pct { font-weight: 700; font-size: var(--font-size-base); }

.error-msg {
  margin-top: 12px;
  padding: 10px 12px;
  background: #fef2f2;
  border: 1px solid #fca5a5;
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  color: #b91c1c;
}

@media (max-width: 640px) {
  .stage__label { display: none; }
}
</style>
