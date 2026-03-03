<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import type { TaskState } from '@/stores/task'
import { submitRepository, getTaskStatus } from '@/api/repositories'
import { useTaskStore } from '@/stores/task'
import { useEventSource } from '@/composables/useEventSource'
import { getSystemConfig } from '@/api/system'
import ProgressBar from '@/components/ProgressBar.vue'

const taskStore = useTaskStore()
const { connectSSE, closeSSE } = useEventSource()

// 表单字段
const repoUrl = ref('')
const patToken = ref('')
const branch = ref('main')
const llmProvider = ref('')
const llmModel = ref('')
const isSubmitting = ref(false)
const submitError = ref('')
const showAdvanced = ref(false)

// 功能特性数据
const features = [
  { icon: '🔍', title: '深度语义解析', desc: '基于 Tree-sitter AST 解析，理解函数、类、模块间的真实依赖关系' },
  { icon: '📖', title: '结构化 Wiki 文档', desc: '自动生成多层级文档，包含架构图（Mermaid）、代码引用和关键路径分析' },
  { icon: '💬', title: 'AI 代码问答', desc: '基于 RAG 的多轮对话，精准回答代码相关问题并附带源码引用' },
  { icon: '⚡', title: '增量同步', desc: '基于 git diff 的智能增量更新，仅处理变更文件，高效快速' },
]

const TERMINAL = ['completed', 'failed', 'cancelled', 'interrupted']

// 每个任务的进度详情展开状态
const showProgress = ref<Record<string, boolean>>({})

const hasTasks = computed(() => taskStore.activeTasks.length > 0)

// 每个任务的辅助函数
function taskTitle(t: TaskState): string {
  const inc = t.type === 'incremental_sync'
  if (t.status === 'completed') return inc ? '增量更新完成' : 'Wiki 生成完成'
  if (t.status === 'failed') return '处理失败'
  if (t.status === 'cancelled') return '任务已中止'
  if (t.status === 'interrupted') return '任务已中断'
  return inc ? '正在增量更新仓库...' : '正在处理仓库...'
}

function bannerMod(t: TaskState): string {
  if (t.status === 'completed') return 'done'
  if (t.status === 'failed') return 'failed'
  if (t.status === 'cancelled' || t.status === 'interrupted') return 'stopped'
  return 'running'
}

function isTerminal(t: TaskState): boolean {
  return TERMINAL.includes(t.status)
}

function toggleProgress(taskId: string) {
  showProgress.value[taskId] = !showProgress.value[taskId]
}

function dismissTask(taskId: string) {
  closeSSE(taskId)
  taskStore.clearTask(taskId)
  delete showProgress.value[taskId]
}

// 页面挂载：从 localStorage 恢复所有活跃任务
onMounted(async () => {
  let ids: string[] = []

  const stored = localStorage.getItem('activeTaskIds')
  if (stored) {
    try { ids = JSON.parse(stored) } catch { /* ignore */ }
  }
  // 向后兼容：单任务 ID
  if (ids.length === 0) {
    const params = new URLSearchParams(window.location.search)
    const single = params.get('taskId') || localStorage.getItem('activeTaskId')
    if (single) ids = [single]
  }

  const validIds: string[] = []
  await Promise.all(ids.map(async (taskId) => {
    try {
      const task = await getTaskStatus(taskId)
      taskStore.setTask({
        id: task.id,
        repoId: task.repo_id,
        type: task.type,
        status: task.status,
        progressPct: task.progress_pct,
        currentStage: task.current_stage || '',
        filesTotal: task.files_total || 0,
        filesProcessed: task.files_processed || 0,
        errorMsg: task.error_msg,
        wikiId: null,
      })
      validIds.push(taskId)
      if (!TERMINAL.includes(task.status)) connectSSE(taskId)
    } catch { /* 任务不存在，跳过 */ }
  }))

  // 清理失效的 ID
  if (validIds.length !== ids.length) {
    if (validIds.length > 0) {
      localStorage.setItem('activeTaskIds', JSON.stringify(validIds))
    } else {
      localStorage.removeItem('activeTaskIds')
      localStorage.removeItem('activeTaskId')
    }
  }
  history.replaceState(null, '', window.location.pathname)

  // 加载已保存的系统配置，预填充 LLM 供应商和模型
  try {
    const cfg = await getSystemConfig()
    if (cfg.is_customized) {
      if (cfg.llm.default_provider) llmProvider.value = cfg.llm.default_provider
      if (cfg.llm.default_model)    llmModel.value    = cfg.llm.default_model
    }
  } catch { /* 后端不可用时静默忽略，保持默认空值 */ }
})

// 提交仓库
async function handleSubmit() {
  if (!repoUrl.value.trim()) return
  isSubmitting.value = true
  submitError.value = ''

  try {
    const result = await submitRepository({
      url: repoUrl.value.trim(),
      pat_token: patToken.value || undefined,
      branch: branch.value || undefined,
      llm_provider: llmProvider.value || undefined,
      llm_model: llmModel.value || undefined,
    })

    taskStore.setTask({
      id: result.task_id,
      repoId: result.repo_id,
      type: 'full_process',
      status: 'pending',
      progressPct: 0,
      currentStage: '任务已提交，等待处理...',
      filesTotal: 0,
      filesProcessed: 0,
      errorMsg: null,
      wikiId: null,
    })

    connectSSE(result.task_id)
    showProgress.value[result.task_id] = true
    repoUrl.value = ''

  } catch (err: unknown) {
    const error = err as { response?: { status?: number; data?: { detail?: unknown } } }
    if (error.response?.status === 409) {
      submitError.value = '该仓库正在处理中，请稍后再试'
      const detail = error.response.data?.detail as { existing_task_id?: string } | undefined
      const existingTaskId = detail?.existing_task_id
      if (existingTaskId) connectSSE(existingTaskId)
    } else if (error.response?.status === 400) {
      const detail = error.response.data?.detail
      submitError.value = (typeof detail === 'string' ? detail : null) || 'URL 格式无效，请检查后重试'
    } else {
      submitError.value = '提交失败，请检查后端服务是否正常运行'
    }
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="home-view">
    <!-- 首页表单（始终显示） -->
    <div class="home-form-container">
      <!-- Hero 区域 -->
      <div class="hero">
        <h1 class="hero__title">Open DeepWiki</h1>
        <p class="hero__badge">AI-powered code knowledge base</p>
        <p class="hero__desc">
          输入任意 Git 仓库地址，AI 自动解析代码，生成结构化知识库文档
        </p>
      </div>

      <!-- 提交表单 -->
      <div class="submit-card">
        <div class="form-group">
          <label class="form-label">仓库地址 <span class="required">*</span></label>
          <div class="url-input-row">
            <input
              v-model="repoUrl"
              class="form-input"
              type="url"
              placeholder="https://github.com/owner/repo"
              @keydown.enter="handleSubmit"
              :disabled="isSubmitting"
            />
            <button
              class="btn btn-primary btn-lg"
              :disabled="isSubmitting || !repoUrl.trim()"
              @click="handleSubmit"
            >
              <span v-if="isSubmitting">
                <span class="spinner" style="width:18px;height:18px;border-width:2px;" />
                提交中...
              </span>
              <span v-else>生成 Wiki</span>
            </button>
          </div>
          <p class="form-hint">支持 GitHub、GitLab、Bitbucket 公开/私有仓库</p>
        </div>

        <!-- 错误提示 -->
        <div v-if="submitError" class="alert alert-error">{{ submitError }}</div>

        <!-- 高级选项折叠 -->
        <button class="advanced-toggle" @click="showAdvanced = !showAdvanced">
          <svg
            class="advanced-toggle__chevron"
            :class="{ 'advanced-toggle__chevron--open': showAdvanced }"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          高级选项（LLM 配置、私有仓库）
        </button>

        <div v-if="showAdvanced" class="advanced-options">
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">分支</label>
              <input v-model="branch" class="form-input" placeholder="main" :disabled="isSubmitting" />
            </div>
            <div class="form-group">
              <label class="form-label">PAT Token（私有仓库）</label>
              <input
                v-model="patToken"
                class="form-input"
                type="password"
                placeholder="ghp_xxxxxxxxxx（用后即毁）"
                :disabled="isSubmitting"
              />
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">LLM 供应商</label>
              <select v-model="llmProvider" class="form-input form-select" :disabled="isSubmitting">
                <option value="">默认（环境变量配置）</option>
                <option value="openai">OpenAI</option>
                <option value="dashscope">DashScope（阿里云）</option>
                <option value="gemini">Google Gemini</option>
                <option value="custom">自定义</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">模型名称</label>
              <input
                v-model="llmModel"
                class="form-input"
                placeholder="如 gpt-4o / qwen-plus"
                :disabled="isSubmitting"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- 任务列表横幅（每个活跃任务一行） -->
      <div v-if="hasTasks" class="task-banners">
        <div
          v-for="task in taskStore.activeTasks"
          :key="task.id"
          class="task-banner"
          :class="`task-banner--${bannerMod(task)}`"
        >
          <div class="task-banner__left">
            <svg v-if="!isTerminal(task)" class="task-banner__icon task-banner__icon--spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" stroke-linecap="round"/></svg>
            <svg v-else-if="task.status === 'completed'" class="task-banner__icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>
            <svg v-else-if="task.status === 'failed'" class="task-banner__icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>
            <svg v-else class="task-banner__icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM7 8a1 1 0 012 0v4a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>
            <div class="task-banner__info">
              <span class="task-banner__title">{{ taskTitle(task) }}</span>
              <span class="task-banner__sub">{{ task.currentStage || '等待中...' }}</span>
            </div>
          </div>
          <div class="task-banner__right">
            <span class="task-banner__pct">{{ Math.round(task.progressPct) }}%</span>
            <RouterLink
              v-if="task.status === 'completed' && task.repoId"
              :to="{ name: 'wiki', params: { repoId: task.repoId } }"
              class="btn btn-primary btn-sm"
            >查看 Wiki</RouterLink>
            <button class="btn btn-secondary btn-sm" @click="toggleProgress(task.id)">
              {{ showProgress[task.id] ? '收起' : '详情' }}
            </button>
            <button class="btn btn-ghost btn-sm" @click="dismissTask(task.id)" title="清除">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px"><path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round"/></svg>
            </button>
          </div>
        </div>
      </div>

      <!-- 功能特性展示 -->
      <div class="features">
        <div class="feature-card" v-for="f in features" :key="f.title">
          <div class="feature-icon">{{ f.icon }}</div>
          <h3>{{ f.title }}</h3>
          <p>{{ f.desc }}</p>
        </div>
      </div>
    </div>

    <!-- 进度详情（每个任务独立展开） -->
    <template v-for="task in taskStore.activeTasks" :key="task.id">
      <div v-if="showProgress[task.id]" class="task-container">
        <div class="task-header">
          <h2 class="task-title">
            <span :class="`task-title__status task-title__status--${bannerMod(task) === 'running' ? 'running' : bannerMod(task) === 'done' ? 'done' : bannerMod(task) === 'failed' ? 'failed' : 'stopped'}`">
              <svg v-if="bannerMod(task) === 'running'" class="task-title__spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" stroke-linecap="round"/></svg>
              <svg v-else-if="bannerMod(task) === 'done'" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>
              <svg v-else-if="bannerMod(task) === 'failed'" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>
              <svg v-else viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM7 8a1 1 0 012 0v4a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>
              {{ taskTitle(task) }}
            </span>
          </h2>
          <div class="task-actions">
            <RouterLink
              v-if="task.status === 'completed' && task.repoId"
              :to="{ name: 'wiki', params: { repoId: task.repoId } }"
              class="btn btn-primary btn-sm"
            >
              查看 Wiki
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;margin-left:4px;vertical-align:-1px"><path d="M3 8h10M9 4l4 4-4 4" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </RouterLink>
          </div>
        </div>

        <ProgressBar
          :status="task.status"
          :progress-pct="task.progressPct"
          :current-stage="task.currentStage"
          :files-processed="task.filesProcessed"
          :files-total="task.filesTotal"
          :error-msg="task.errorMsg"
        />

        <div class="task-info">
          <div class="info-item">
            <span class="info-label">任务 ID</span>
            <code class="info-value">{{ task.id }}</code>
          </div>
          <div class="info-item">
            <span class="info-label">仓库 ID</span>
            <code class="info-value">{{ task.repoId }}</code>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.home-view {
  max-width: 860px;
  margin: 0 auto;
  padding: 48px 20px 80px;
  width: 100%;
}

/* ── Hero ─────────────────────────────────────────── */
.hero {
  text-align: center;
  margin-bottom: 40px;
}

.hero__title {
  font-size: 2.75rem;
  font-weight: 800;
  margin-bottom: 10px;
  background: linear-gradient(135deg, #1e40af, #6d28d9);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: -0.025em;
  line-height: 1.1;
}

.hero__badge {
  display: inline-block;
  font-size: 13px;
  color: var(--text-muted);
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  padding: 3px 12px;
  border-radius: var(--radius-full);
  margin-bottom: 12px;
}

.hero__desc {
  font-size: 15px;
  color: var(--text-tertiary);
  max-width: 520px;
  margin: 0 auto;
  line-height: 1.7;
}

/* ── Submit card ──────────────────────────────────── */
.submit-card {
  margin-bottom: 28px;
  border-radius: var(--radius-lg);
  padding: 24px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  box-shadow: var(--shadow);
}

.url-input-row {
  display: flex;
  gap: 10px;
}

.url-input-row .form-input {
  flex: 1;
}

.required { color: #ef4444; }

.advanced-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
  cursor: pointer;
  padding: 4px 0;
  margin-top: 10px;
  transition: color 0.15s;
}
.advanced-toggle:hover { color: var(--text-secondary); }

.advanced-toggle__chevron {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  transition: transform 0.2s;
}

.advanced-toggle__chevron--open {
  transform: rotate(180deg);
}

.advanced-options {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

/* ── Feature cards ────────────────────────────────── */
.features {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px;
  margin-top: 8px;
}

.feature-card {
  padding: 18px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  transition: all 0.2s;
}

.feature-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.feature-icon {
  font-size: 24px;
  margin-bottom: 10px;
  line-height: 1;
}

.feature-card h3 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.feature-card p {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  line-height: 1.6;
}

/* ── Task banner ──────────────────────────────────── */
.task-banners {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 20px;
}

.task-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border-color);
  flex-wrap: wrap;
}

.task-banner--running {
  background: #eff6ff;
  border-color: #bfdbfe;
}
.task-banner--done {
  background: #f0fdf4;
  border-color: #bbf7d0;
}
.task-banner--failed {
  background: #fef2f2;
  border-color: #fca5a5;
}
.task-banner--stopped {
  background: #fffbeb;
  border-color: #fde68a;
}

[data-theme="dark"] .task-banner--running { background: rgba(37,99,235,0.1); border-color: rgba(37,99,235,0.3); }
[data-theme="dark"] .task-banner--done { background: rgba(16,185,129,0.1); border-color: rgba(16,185,129,0.3); }
[data-theme="dark"] .task-banner--failed { background: rgba(239,68,68,0.1); border-color: rgba(239,68,68,0.3); }
[data-theme="dark"] .task-banner--stopped { background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.3); }

.task-banner__left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.task-banner__icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.task-banner--running .task-banner__icon { color: #2563eb; }
.task-banner--done .task-banner__icon { color: #059669; }
.task-banner--failed .task-banner__icon { color: #dc2626; }
.task-banner--stopped .task-banner__icon { color: #d97706; }

.task-banner__icon--spin {
  animation: spin 1.4s linear infinite;
}

.task-banner__info {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.task-banner__title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-banner__sub {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-banner__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.task-banner__pct {
  font-size: var(--font-size-sm);
  font-weight: 700;
  color: var(--text-secondary);
  min-width: 36px;
  text-align: right;
}

.btn-ghost {
  background: none;
  border: 1px solid transparent;
  color: var(--text-muted);
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  transition: all 0.15s;
}
.btn-ghost:hover {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

/* ── Task container ───────────────────────────────── */
.task-container {
  max-width: 700px;
  margin: 0 auto;
}

.task-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.task-title {
  font-size: var(--font-size-xl);
  font-weight: 600;
  color: var(--text-primary);
}

.task-title__status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.task-title__status svg {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.task-title__status--done { color: #059669; }
.task-title__status--failed { color: #dc2626; }
.task-title__status--stopped { color: #d97706; }
.task-title__status--running { color: var(--text-primary); }

.task-title__spin {
  animation: spin 1.4s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.task-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.task-info {
  margin-top: 16px;
  padding: 12px 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-label {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}

.info-value {
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}

/* ── Responsive ───────────────────────────────────── */
@media (max-width: 640px) {
  .home-view { padding-top: 24px; }
  .url-input-row { flex-direction: column; }
  .form-row { grid-template-columns: 1fr; }
  .hero__title { font-size: 1.75rem; }
  .hero__subtitle { font-size: 0.9rem; }
  .task-banner { word-break: break-word; }
  .features { grid-template-columns: 1fr; }
}

@media (max-width: 380px) {
  .hero__title { font-size: 1.5rem; }
}
</style>
