<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { submitRepository, getTaskStatus } from '@/api/repositories'
import { useTaskStore } from '@/stores/task'
import { useEventSource } from '@/composables/useEventSource'
import ProgressBar from '@/components/ProgressBar.vue'

const taskStore = useTaskStore()
const { connectSSE } = useEventSource()

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
  {
    icon: '🔍',
    title: '深度语义解析',
    desc: '基于 Tree-sitter AST 解析，理解函数、类、模块间的真实依赖关系',
  },
  {
    icon: '📖',
    title: '结构化 Wiki 文档',
    desc: '自动生成多层级文档，包含架构图（Mermaid）、代码引用和关键路径分析',
  },
  {
    icon: '💬',
    title: 'AI 代码问答',
    desc: '基于 RAG 的多轮对话，精准回答代码相关问题并附带源码引用',
  },
  {
    icon: '⚡',
    title: '增量同步',
    desc: '基于 git diff 的智能增量更新，仅处理变更文件，高效快速',
  },
]

// 是否处于进度展示模式
const hasTask = computed(() => taskStore.currentTask !== null)
const isCompleted = computed(() => taskStore.currentTask?.status === 'completed')
const isFailed = computed(() => taskStore.currentTask?.status === 'failed')

// 页面挂载：从 URL 或 localStorage 恢复任务状态
onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const existingTaskId = params.get('taskId') || localStorage.getItem('activeTaskId')

  if (existingTaskId) {
    try {
      const task = await getTaskStatus(existingTaskId)
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
      // 同步 URL，方便分享
      if (!params.get('taskId')) {
        history.replaceState(null, '', `${window.location.pathname}?taskId=${existingTaskId}`)
      }
      // 若任务仍在进行中，重连 SSE
      if (!['completed', 'failed', 'cancelled'].includes(task.status)) {
        connectSSE(existingTaskId)
      }
    } catch {
      // 任务不存在，清除缓存
      localStorage.removeItem('activeTaskId')
      history.replaceState(null, '', window.location.pathname)
    }
  }
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

    // 初始化任务状态
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

    // 静默重写 URL（不触发 Vue Router 导航）
    history.pushState(
      { taskId: result.task_id },
      '',
      `${window.location.pathname}?taskId=${result.task_id}`
    )

    // 连接 SSE
    connectSSE(result.task_id)

  } catch (err: unknown) {
    const error = err as { response?: { status?: number; data?: { detail?: unknown } } }
    if (error.response?.status === 409) {
      submitError.value = '该仓库正在处理中，请稍后再试'
      const detail = error.response.data?.detail as { existing_task_id?: string } | undefined
      const existingTaskId = detail?.existing_task_id
      if (existingTaskId) {
        history.pushState(null, '', `?taskId=${existingTaskId}`)
        connectSSE(existingTaskId)
      }
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

// 重置，提交新任务
function resetAndSubmitNew() {
  taskStore.clearTask()
  repoUrl.value = ''
  history.replaceState(null, '', window.location.pathname)
}
</script>

<template>
  <div class="home-view">
    <!-- 未提交状态：显示提交表单 -->
    <div v-if="!hasTask" class="home-form-container">
      <!-- Hero 区域 -->
      <div class="hero">
        <div class="hero__icon">📚</div>
        <h1 class="hero__title">Open DeepWiki</h1>
        <p class="hero__desc">
          输入任意 Git 仓库地址，AI 自动解析代码，生成结构化知识库文档
        </p>
      </div>

      <!-- 提交表单 -->
      <div class="submit-card card">
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
          {{ showAdvanced ? '▾' : '▸' }} 高级选项（LLM 配置、私有仓库）
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

      <!-- 功能特性展示 -->
      <div class="features">
        <div class="feature-card" v-for="f in features" :key="f.title">
          <div class="feature-icon">{{ f.icon }}</div>
          <h3>{{ f.title }}</h3>
          <p>{{ f.desc }}</p>
        </div>
      </div>
    </div>

    <!-- 任务进行中/完成状态：显示进度 -->
    <div v-else class="task-container">
      <div class="task-header">
        <h2 class="task-title">
          <span v-if="isCompleted">✅ Wiki 生成完成</span>
          <span v-else-if="isFailed">❌ 处理失败</span>
          <span v-else>⚙️ 正在处理仓库...</span>
        </h2>
        <div class="task-actions">
          <button class="btn btn-secondary btn-sm" @click="resetAndSubmitNew">
            提交新仓库
          </button>
          <RouterLink
            v-if="isCompleted && taskStore.currentTask?.repoId"
            :to="{ name: 'wiki', params: { repoId: taskStore.currentTask.repoId } }"
            class="btn btn-primary"
          >
            查看 Wiki →
          </RouterLink>
        </div>
      </div>

      <!-- 进度条 -->
      <ProgressBar
        :status="taskStore.currentTask!.status"
        :progress-pct="taskStore.currentTask!.progressPct"
        :current-stage="taskStore.currentTask!.currentStage"
        :files-processed="taskStore.currentTask!.filesProcessed"
        :files-total="taskStore.currentTask!.filesTotal"
        :error-msg="taskStore.currentTask!.errorMsg"
      />

      <!-- 任务信息 -->
      <div class="task-info">
        <div class="info-item">
          <span class="info-label">任务 ID</span>
          <code class="info-value">{{ taskStore.currentTask!.id }}</code>
        </div>
        <div class="info-item">
          <span class="info-label">仓库 ID</span>
          <code class="info-value">{{ taskStore.currentTask!.repoId }}</code>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-view {
  max-width: 900px;
  margin: 0 auto;
  padding: 40px 20px;
  width: 100%;
}

.hero {
  text-align: center;
  margin-bottom: 36px;
}

.hero__icon {
  font-size: 52px;
  margin-bottom: 12px;
}

.hero__title {
  font-size: 2.5rem;
  font-weight: 800;
  margin-bottom: 12px;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.hero__desc {
  font-size: var(--font-size-lg);
  color: var(--text-tertiary);
  max-width: 600px;
  margin: 0 auto;
}

.submit-card {
  margin-bottom: 24px;
}

.url-input-row {
  display: flex;
  gap: 12px;
}

.url-input-row .form-input {
  flex: 1;
}

.required { color: #ef4444; }

.advanced-toggle {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: var(--font-size-sm);
  cursor: pointer;
  padding: 4px 0;
  margin-top: 8px;
}
.advanced-toggle:hover { color: var(--text-secondary); }

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

.features {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
}

.feature-card {
  padding: 20px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  transition: box-shadow 0.2s;
}

.feature-card:hover { box-shadow: var(--shadow-md); }

.feature-icon { font-size: 28px; margin-bottom: 8px; }

.feature-card h3 {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.feature-card p {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  line-height: 1.5;
}

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

.task-actions {
  display: flex;
  gap: 8px;
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

@media (max-width: 640px) {
  .url-input-row { flex-direction: column; }
  .form-row { grid-template-columns: 1fr; }
  .hero__title { font-size: 1.8rem; }
}
</style>
