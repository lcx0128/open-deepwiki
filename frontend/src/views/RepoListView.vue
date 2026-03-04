<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { getRepositories, deleteRepository, reprocessRepository, syncRepository, abortRepository, getPendingCommits, type CommitInfo } from '@/api/repositories'
import { regenerateWiki } from '@/api/wiki'
import { getSystemConfig } from '@/api/system'
import { useRepoStore } from '@/stores/repo'
import { useTaskStore } from '@/stores/task'
import { useEventSource } from '@/composables/useEventSource'
import StatusBadge from '@/components/StatusBadge.vue'
import type { RepositoryItem } from '@/api/repositories'

const router = useRouter()
const repoStore = useRepoStore()
const taskStore = useTaskStore()
const { connectSSE } = useEventSource()

const filterStatus = ref('')
const deleteTarget = ref<RepositoryItem | null>(null)
const abortTarget = ref<RepositoryItem | null>(null)
const actionLoading = ref<string | null>(null) // repoId
const syncTarget = ref<RepositoryItem | null>(null)  // 待增量更新的仓库
const showSyncModal = ref(false)
const syncLlmProvider = ref('')
const syncLlmModel = ref('')
const showSyncAdvanced = ref(false)
const pendingCommits = ref<CommitInfo[]>([])
const pendingCommitsLoading = ref(false)
const pendingCommitsError = ref('')
const showPendingCommits = ref(false)
const pendingCommitsBranch = ref('')

// 重新处理弹窗状态
const reprocessTarget = ref<RepositoryItem | null>(null)
const showReprocessModal = ref(false)
const reprocessBranch = ref('')
const reprocessPatToken = ref('')
const reprocessLlmProvider = ref('')
const reprocessLlmModel = ref('')
const showReprocessAdvanced = ref(false)

// 已保存的系统默认 LLM 配置（用于预填充对话框）
const defaultLlmProvider = ref('')
const defaultLlmModel = ref('')

const filteredRepos = computed(() => {
  if (!filterStatus.value) return repoStore.repos
  return repoStore.repos.filter(r => r.status === filterStatus.value)
})

async function loadRepos() {
  repoStore.isLoading = true
  try {
    const data = await getRepositories(repoStore.page, repoStore.perPage)
    repoStore.setRepos(data.items, data.total)
  } catch {
    repoStore.error = '加载仓库列表失败'
  } finally {
    repoStore.isLoading = false
  }
}

async function handleDelete() {
  if (!deleteTarget.value) return
  const repoId = deleteTarget.value.id
  deleteTarget.value = null
  actionLoading.value = repoId

  try {
    await deleteRepository(repoId)
    repoStore.removeRepo(repoId)
  } catch {
    repoStore.error = '删除失败'
  } finally {
    actionLoading.value = null
  }
}

async function handleAbort() {
  if (!abortTarget.value) return
  const repo = abortTarget.value
  abortTarget.value = null
  actionLoading.value = repo.id
  try {
    await abortRepository(repo.id)
    repoStore.updateRepoStatus(repo.id, 'interrupted')
  } catch {
    repoStore.error = '中止失败，请稍后重试'
  } finally {
    actionLoading.value = null
  }
}

async function handleReprocess(repo: RepositoryItem) {
  reprocessTarget.value = repo
  reprocessBranch.value = repo.default_branch || 'main'
  reprocessPatToken.value = ''
  reprocessLlmProvider.value = defaultLlmProvider.value
  reprocessLlmModel.value = defaultLlmModel.value
  showReprocessAdvanced.value = false
  showReprocessModal.value = true
}

async function confirmReprocess() {
  if (!reprocessTarget.value) return
  const repo = reprocessTarget.value
  showReprocessModal.value = false
  actionLoading.value = repo.id
  try {
    const result = await reprocessRepository(repo.id, {
      branch: reprocessBranch.value || undefined,
      pat_token: reprocessPatToken.value || undefined,
      llm_provider: reprocessLlmProvider.value || undefined,
      llm_model: reprocessLlmModel.value || undefined,
    })
    taskStore.setTask({
      id: result.task_id,
      repoId: repo.id,
      type: 'full_process',
      status: 'pending',
      progressPct: 0,
      currentStage: '重新处理已开始...',
      filesTotal: 0,
      filesProcessed: 0,
      errorMsg: null,
      wikiId: null,
    })
    connectSSE(result.task_id)
    repoStore.updateRepoStatus(repo.id, 'cloning')
    router.push({ path: '/', query: { taskId: result.task_id } })
  } catch {
    repoStore.error = '重新处理失败'
  } finally {
    actionLoading.value = null
  }
}

async function handleRegenerate(repo: RepositoryItem) {
  actionLoading.value = repo.id
  try {
    const requestData: { llm_provider?: string; llm_model?: string } = {}
    if (defaultLlmProvider.value) requestData.llm_provider = defaultLlmProvider.value
    if (defaultLlmModel.value)    requestData.llm_model    = defaultLlmModel.value
    const result = await regenerateWiki(repo.id, requestData)
    taskStore.setTask({
      id: result.task_id,
      repoId: repo.id,
      type: 'wiki_regenerate',
      status: 'pending',
      progressPct: 0,
      currentStage: 'Wiki 重新生成已开始...',
      filesTotal: 0,
      filesProcessed: 0,
      errorMsg: null,
      wikiId: null,
    })
    connectSSE(result.task_id)
    repoStore.updateRepoStatus(repo.id, 'generating' as RepositoryItem['status'])
    router.push({ path: '/', query: { taskId: result.task_id } })
  } catch {
    repoStore.error = '重新生成 Wiki 失败'
  } finally {
    actionLoading.value = null
  }
}

function stageLabel(stage: string | null | undefined): string {
  const map: Record<string, string> = {
    cloning: '克隆',
    parsing: '解析',
    embedding: '向量化',
    generating: 'Wiki生成',
  }
  return stage ? (map[stage] || stage) : ''
}

async function handleSync(repo: RepositoryItem) {
  syncTarget.value = repo
  showSyncModal.value = true
  syncLlmProvider.value = defaultLlmProvider.value
  syncLlmModel.value = defaultLlmModel.value
  showSyncAdvanced.value = false
  // 重置提交列表状态
  pendingCommits.value = []
  pendingCommitsLoading.value = false
  pendingCommitsError.value = ''
  showPendingCommits.value = false
  pendingCommitsBranch.value = ''
}

async function loadPendingCommits() {
  if (!syncTarget.value) return
  pendingCommitsLoading.value = true
  pendingCommitsError.value = ''
  showPendingCommits.value = true
  try {
    const result = await getPendingCommits(syncTarget.value.id)
    pendingCommits.value = result.commits
    pendingCommitsBranch.value = result.branch
  } catch {
    pendingCommitsError.value = '获取提交列表失败，请检查网络连接'
  } finally {
    pendingCommitsLoading.value = false
  }
}

async function confirmSync() {
  if (!syncTarget.value) return
  const repo = syncTarget.value
  showSyncModal.value = false
  actionLoading.value = repo.id

  try {
    const result = await syncRepository(repo.id, {
      llm_provider: syncLlmProvider.value || undefined,
      llm_model: syncLlmModel.value || undefined,
    })
    taskStore.setTask({
      id: result.task_id,
      repoId: repo.id,
      type: 'incremental_sync',
      status: 'pending',
      progressPct: 0,
      currentStage: '增量同步已开始...',
      filesTotal: 0,
      filesProcessed: 0,
      errorMsg: null,
      wikiId: null,
    })
    connectSSE(result.task_id)
    repoStore.updateRepoStatus(repo.id, 'syncing')
    router.push({ path: '/', query: { taskId: result.task_id } })
  } catch (err: unknown) {
    const e = err as { response?: { status?: number; data?: { detail?: unknown } } }
    if (e.response?.status === 409) {
      repoStore.error = '该仓库正在处理中，请稍后再试'
    } else if (e.response?.status === 400) {
      const d = e.response.data?.detail
      repoStore.error = (typeof d === 'string' ? d : null) || '增量同步失败'
    } else {
      repoStore.error = '增量同步失败，请检查后端服务'
    }
  } finally {
    actionLoading.value = null
    syncTarget.value = null
  }
}

function formatDate(dateStr: string | null) {
  if (!dateStr) return '从未同步'
  // 后端返回 naive datetime（无时区后缀），需补 Z 告知 JS 这是 UTC，
  // 否则 JS 会将其当本地时间解析，导致 UTC+8 下显示时间偏早 8 小时
  const normalized = /Z|[+-]\d{2}:\d{2}$/.test(dateStr) ? dateStr : dateStr + 'Z'
  return new Date(normalized).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(async () => {
  await loadRepos()
  try {
    const cfg = await getSystemConfig()
    if (cfg.is_customized) {
      if (cfg.llm.default_provider) defaultLlmProvider.value = cfg.llm.default_provider
      if (cfg.llm.default_model)    defaultLlmModel.value    = cfg.llm.default_model
    }
  } catch { /* 静默忽略 */ }
})
</script>

<template>
  <div class="repo-list-view">
    <!-- 页面标题 -->
    <div class="page-header">
      <div>
        <h1 class="page-title">仓库管理</h1>
        <p class="page-desc">管理已处理的代码仓库与 Wiki 文档</p>
      </div>
      <RouterLink to="/" class="btn btn-primary">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" style="width:14px;height:14px;margin-right:5px;vertical-align:-1px">
          <path d="M8 2v12M2 8h12" stroke-linecap="round"/>
        </svg>
        添加仓库
      </RouterLink>
    </div>

    <!-- 过滤栏 -->
    <div class="filter-bar">
      <div class="filter-group">
        <button
          class="filter-btn"
          :class="{ 'filter-btn--active': filterStatus === '' }"
          @click="filterStatus = ''"
        >全部 ({{ repoStore.total }})</button>
        <button
          class="filter-btn"
          :class="{ 'filter-btn--active': filterStatus === 'ready' }"
          @click="filterStatus = 'ready'"
        >就绪</button>
        <button
          class="filter-btn"
          :class="{ 'filter-btn--active': filterStatus === 'error' }"
          @click="filterStatus = 'error'"
        >失败</button>
        <button
          class="filter-btn"
          :class="{ 'filter-btn--active': filterStatus === 'pending' }"
          @click="filterStatus = 'pending'"
        >处理中</button>
      </div>
      <button class="btn btn-ghost btn-sm refresh-btn" @click="loadRepos" :disabled="repoStore.isLoading">
        <svg
          class="refresh-icon"
          :class="{ 'refresh-icon--spinning': repoStore.isLoading }"
          viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
        >
          <path d="M13.5 2.5A7 7 0 1 0 14 8" stroke-linecap="round"/>
          <path d="M14 2.5V6h-3.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        {{ repoStore.isLoading ? '刷新中...' : '刷新' }}
      </button>
    </div>

    <!-- 错误 -->
    <div v-if="repoStore.error" class="alert alert-error">{{ repoStore.error }}</div>

    <!-- 加载中 -->
    <div v-if="repoStore.isLoading && repoStore.repos.length === 0" class="list-loading">
      <span class="spinner" />
      <span>加载中...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="filteredRepos.length === 0" class="list-empty">
      <div class="list-empty__icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <h3>暂无仓库</h3>
      <p>还没有处理过任何仓库，去首页添加一个吧</p>
      <RouterLink to="/" class="btn btn-primary">添加仓库</RouterLink>
    </div>

    <!-- 仓库列表 -->
    <div v-else class="repo-grid">
      <div
        v-for="repo in filteredRepos"
        :key="repo.id"
        class="repo-card card"
      >
        <!-- 仓库信息 -->
        <div class="repo-card__header">
          <div class="repo-card__title-area">
            <div class="repo-card__platform">
              <span class="platform-icon">
                {{ repo.platform === 'github' ? '⬡' : repo.platform === 'gitlab' ? '🦊' : '☁' }}
              </span>
              <span class="platform-name">{{ repo.platform }}</span>
              <span class="branch-badge">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <circle cx="5" cy="3.5" r="1.5"/>
                  <circle cx="5" cy="12.5" r="1.5"/>
                  <circle cx="11" cy="6.5" r="1.5"/>
                  <path d="M5 5v5" stroke-linecap="round"/>
                  <path d="M5 5.5C5 7.5 11 7.5 11 8" stroke-linecap="round"/>
                </svg>
                {{ repo.default_branch || 'main' }}
              </span>
            </div>
            <h3 class="repo-card__name">{{ repo.name }}</h3>
          </div>
          <StatusBadge :status="repo.status" />
          <span
            v-if="repo.status === 'error' && repo.failed_at_stage"
            class="failed-stage-hint"
          >{{ stageLabel(repo.failed_at_stage) }}失败</span>
        </div>

        <p class="repo-card__url">{{ repo.url }}</p>

        <div class="repo-card__meta">
          <span>最后同步：{{ formatDate(repo.last_synced_at) }}</span>
          <span>创建：{{ formatDate(repo.created_at) }}</span>
        </div>

        <!-- 操作按钮 -->
        <div class="repo-card__actions">
          <RouterLink
            v-if="repo.status === 'ready'"
            :to="{ name: 'wiki', params: { repoId: repo.id } }"
            class="btn btn-primary btn-sm"
          >
            查看 Wiki
          </RouterLink>
          <RouterLink
            v-if="repo.status === 'ready'"
            :to="{ name: 'chat', params: { repoId: repo.id } }"
            class="btn btn-secondary btn-sm"
          >
            AI 问答
          </RouterLink>
          <button
            v-if="repo.status === 'ready'"
            class="btn btn-secondary btn-sm"
            :disabled="actionLoading === repo.id"
            @click="handleSync(repo)"
          >
            增量更新
          </button>
          <!-- 中止按钮：任务进行中时显示 -->
          <button
            v-if="['pending', 'cloning', 'parsing', 'embedding', 'generating', 'syncing'].includes(repo.status)"
            class="btn btn-ghost btn-sm btn-warning-ghost"
            :disabled="actionLoading === repo.id"
            @click="abortTarget = repo"
          >
            中止
          </button>
          <!-- 重新处理：已中断或失败时显示 -->
          <button
            v-if="repo.status === 'interrupted' || (repo.status === 'error' && repo.failed_at_stage === 'generating')"
            class="btn btn-primary btn-sm"
            :disabled="actionLoading === repo.id"
            @click="handleReprocess(repo)"
          >
            <span v-if="actionLoading === repo.id">处理中...</span>
            <span v-else>重新处理</span>
          </button>
          <button
            v-if="repo.status === 'error' && repo.failed_at_stage === 'generating'"
            class="btn btn-secondary btn-sm"
            :disabled="actionLoading === repo.id"
            @click="handleRegenerate(repo)"
          >重新生成 Wiki</button>
          <button
            v-if="!['pending', 'cloning', 'parsing', 'embedding', 'generating', 'syncing', 'interrupted'].includes(repo.status) && !(repo.status === 'error' && repo.failed_at_stage === 'generating')"
            class="btn btn-secondary btn-sm"
            :disabled="actionLoading === repo.id"
            @click="handleReprocess(repo)"
          >
            <span v-if="actionLoading === repo.id">处理中...</span>
            <span v-else>重新处理</span>
          </button>
          <button
            class="btn btn-ghost btn-sm btn-danger-ghost"
            :disabled="actionLoading === repo.id"
            @click="deleteTarget = repo"
          >
            删除
          </button>
        </div>
      </div>
    </div>

    <!-- 中止确认弹窗 -->
    <div v-if="abortTarget" class="modal-overlay" @click.self="abortTarget = null">
      <div class="modal card">
        <h3>确认中止任务</h3>
        <p>
          将中止仓库 <strong>{{ abortTarget.name }}</strong> 当前所有生成任务。<br><br>
          中止后可点击「重新处理」恢复。
        </p>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="abortTarget = null">取消</button>
          <button class="btn btn-warning" @click="handleAbort">确认中止</button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <div v-if="deleteTarget" class="modal-overlay" @click.self="deleteTarget = null">
      <div class="modal card">
        <h3>确认删除仓库</h3>
        <p>
          将删除仓库 <strong>{{ deleteTarget.name }}</strong> 及其所有 Wiki、向量数据和本地克隆，<strong>不可恢复</strong>。
        </p>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="deleteTarget = null">取消</button>
          <button class="btn btn-danger" @click="handleDelete">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 增量同步弹窗 -->
    <div v-if="showSyncModal" class="modal-overlay" @click.self="showSyncModal = false">      <div class="modal card sync-modal">
        <h3>增量更新仓库</h3>
        <p>
          将对仓库 <strong>{{ syncTarget?.name }}</strong> 执行增量同步：
          拉取最新代码、仅重新处理变更文件并更新 Wiki。
          <br><br>
          <strong>同步期间将暂时无法查看 Wiki。</strong>
          <br>
          <strong>若当前仓库更新过多，建议点击重新生成按钮进行全量更新，增量更新只适合大纲改动不大的场景</strong>
        </p>

        <!-- 待同步提交查看器 -->
        <div class="commits-section">
          <button
            class="advanced-toggle"
            :disabled="pendingCommitsLoading"
            @click="showPendingCommits ? (showPendingCommits = false) : loadPendingCommits()"
          >
            <svg
              class="advanced-toggle__chevron"
              :class="{ 'advanced-toggle__chevron--open': showPendingCommits }"
              viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
            >
              <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span v-if="pendingCommitsLoading">正在获取提交列表...</span>
            <span v-else-if="showPendingCommits && pendingCommits.length > 0">
              隐藏提交列表（{{ pendingCommits.length }} 个新提交，分支：{{ pendingCommitsBranch }}）
            </span>
            <span v-else-if="showPendingCommits && pendingCommits.length === 0">
              隐藏提交列表
            </span>
            <span v-else>查看待同步提交</span>
          </button>

          <div v-if="showPendingCommits" class="commits-panel">
            <div v-if="pendingCommitsLoading" class="commits-loading">
              <span class="spinner spinner--sm" />
              <span>正在 git fetch...</span>
            </div>
            <div v-else-if="pendingCommitsError" class="commits-error">{{ pendingCommitsError }}</div>
            <div v-else-if="pendingCommits.length === 0" class="commits-empty">
              当前分支已是最新，无待同步提交。
            </div>
            <div v-else class="commits-list">
              <div
                v-for="commit in pendingCommits"
                :key="commit.hash"
                class="commit-item"
              >
                <code class="commit-hash">{{ commit.short_hash }}</code>
                <span class="commit-message">{{ commit.message }}</span>
                <span class="commit-meta">{{ commit.author }} · {{ commit.date }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- LLM 高级选项 -->
        <button class="advanced-toggle" @click="showSyncAdvanced = !showSyncAdvanced">
          <svg
            class="advanced-toggle__chevron"
            :class="{ 'advanced-toggle__chevron--open': showSyncAdvanced }"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          LLM 配置（可选）
        </button>

        <div v-if="showSyncAdvanced" class="sync-advanced">
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">LLM 供应商</label>
              <select v-model="syncLlmProvider" class="form-input form-select">
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
                v-model="syncLlmModel"
                class="form-input"
                placeholder="如 gpt-4o / qwen-plus"
              />
            </div>
          </div>
        </div>

        <div class="modal-actions">
          <button class="btn btn-secondary" @click="showSyncModal = false">取消</button>
          <button class="btn btn-primary" @click="confirmSync">开始增量更新</button>
        </div>
      </div>
    </div>

    <!-- 重新处理弹窗 -->
    <div v-if="showReprocessModal" class="modal-overlay" @click.self="showReprocessModal = false">
      <div class="modal card sync-modal">
        <h3>重新处理仓库</h3>
        <p>
          将对仓库 <strong>{{ reprocessTarget?.name }}</strong> 执行完整重新处理：
          重新克隆代码、解析、向量化并重新生成 Wiki。
          <br><br>
          <strong>处理期间将暂时无法查看 Wiki。</strong>
        </p>

        <!-- 分支配置 -->
        <div class="form-group" style="margin-bottom: 12px;">
          <label class="form-label">目标分支</label>
          <input
            v-model="reprocessBranch"
            class="form-input"
            placeholder="如 main / develop / master"
          />
        </div>

        <!-- 私有仓库 Token -->
        <div class="form-group" style="margin-bottom: 12px;">
          <label class="form-label">PAT Token（私有仓库需重新填写）</label>
          <input
            v-model="reprocessPatToken"
            type="password"
            class="form-input"
            placeholder="ghp_xxxxxxxxxx（公开仓库留空）"
          />
          <p class="form-hint">出于安全考虑，Token 不会被持久化存储，重新处理时需再次提供。</p>
        </div>

        <!-- LLM 高级选项 -->
        <button class="advanced-toggle" @click="showReprocessAdvanced = !showReprocessAdvanced">
          <svg
            class="advanced-toggle__chevron"
            :class="{ 'advanced-toggle__chevron--open': showReprocessAdvanced }"
            viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"
          >
            <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          LLM 配置（可选）
        </button>

        <div v-if="showReprocessAdvanced" class="sync-advanced">
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">LLM 供应商</label>
              <select v-model="reprocessLlmProvider" class="form-input form-select">
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
                v-model="reprocessLlmModel"
                class="form-input"
                placeholder="如 gpt-4o / qwen-plus"
              />
            </div>
          </div>
        </div>

        <div class="modal-actions">
          <button class="btn btn-secondary" @click="showReprocessModal = false">取消</button>
          <button class="btn btn-primary" @click="confirmReprocess">开始重新处理</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.repo-list-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 36px 20px 80px;
  width: 100%;
}

/* ── Page header ──────────────────────────────────── */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 28px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border-color);
}

.page-title {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
  letter-spacing: -0.02em;
}

.page-desc {
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
}

/* ── Filter bar ───────────────────────────────────── */
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-group { display: flex; gap: 4px; }

.filter-btn {
  padding: 5px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-primary);
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  font-weight: 500;
}
.filter-btn:hover { background: var(--bg-hover); border-color: var(--border-color-strong); }
.filter-btn--active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.refresh-icon {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
  transition: transform 0.6s;
}

.refresh-icon--spinning {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── Loading / empty ──────────────────────────────── */
.list-loading, .list-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 64px 20px;
  color: var(--text-muted);
}

.list-empty__icon {
  width: 56px;
  height: 56px;
  color: var(--text-muted);
  opacity: 0.6;
}

.list-empty__icon svg {
  width: 100%;
  height: 100%;
}

.list-empty h3 {
  font-size: var(--font-size-xl);
  font-weight: 600;
  color: var(--text-secondary);
}
.list-empty p { font-size: var(--font-size-sm); color: var(--text-muted); }

/* ── Repo grid ────────────────────────────────────── */
.repo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.repo-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: all 0.2s;
  border-radius: var(--radius-lg);
  padding: 18px;
}

.repo-card:hover {
  border-color: var(--border-color-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.repo-card__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}

.repo-card__title-area {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.repo-card__platform {
  display: flex;
  align-items: center;
  gap: 4px;
}

.platform-name {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  text-transform: capitalize;
}

.branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  padding: 1px 7px 1px 5px;
  font-family: var(--font-mono);
  line-height: 1.6;
}

.branch-badge svg {
  width: 11px;
  height: 11px;
  flex-shrink: 0;
  color: var(--text-muted);
}

.repo-card__name {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-card__url {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.repo-card__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}

.repo-card__actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.btn-danger-ghost {
  color: #ef4444;
}
.btn-danger-ghost:hover:not(:disabled) {
  background: #fef2f2;
  color: #dc2626;
}

.btn-warning-ghost {
  color: #d97706;
}
.btn-warning-ghost:hover:not(:disabled) {
  background: #fffbeb;
  color: #b45309;
}

.btn-warning {
  background: #f59e0b;
  color: white;
  border: none;
}
.btn-warning:hover:not(:disabled) {
  background: #d97706;
}

.failed-stage-hint {
  font-size: var(--font-size-xs);
  color: #ef4444;
  font-weight: 500;
  white-space: nowrap;
}

/* ── Delete modal ─────────────────────────────────── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(2px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.modal {
  max-width: 420px;
  width: 100%;
}

.modal h3 { margin-bottom: 12px; font-size: var(--font-size-lg); font-weight: 600; }
.modal p {
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  margin-bottom: 20px;
  line-height: 1.6;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ── Responsive ───────────────────────────────────── */
@media (max-width: 640px) {
  .repo-grid { grid-template-columns: 1fr; }
  .page-header { flex-direction: column; gap: 16px; }
  .page-title { font-size: var(--font-size-xl); }
  .page-header .btn { width: 100%; justify-content: center; }

  /* Form rows collapse to single column inside modals */
  .form-row { grid-template-columns: 1fr; }

  /* Modals get constrained height and proper mobile padding */
  .modal {
    max-height: 90vh;
    overflow-y: auto;
    padding: 20px 16px;
  }
  .sync-modal {
    max-width: 100%;
    max-height: 90vh;
    overflow-y: auto;
    padding: 20px 16px;
  }

  /* Commit grid collapses to single column */
  .commit-item {
    grid-template-columns: auto 1fr;
    grid-template-rows: auto auto;
  }
  .commit-meta {
    grid-column: 1 / -1;
    padding-top: 2px;
  }

  /* Action buttons get adequate touch target size */
  .repo-card__actions { gap: 8px; }
  .repo-card__actions .btn { min-height: 36px; }

  /* Filter bar wraps on small screens */
  .filter-bar { flex-direction: column; align-items: flex-start; }
  .filter-group { flex-wrap: wrap; }
}

@media (max-width: 480px) {
  .repo-list-view { padding: 20px 12px 60px; }

  /* URL text breaks to prevent overflow */
  .repo-card__url {
    white-space: normal;
    word-break: break-all;
  }

  /* Ensure URL input is full width inside modals */
  .modal .form-input,
  .sync-modal .form-input { width: 100%; box-sizing: border-box; }

  /* Modal overlay reduces padding so modal fills screen better */
  .modal-overlay { padding: 12px; }
}

/* ── Sync modal advanced options ──────────────────── */
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
.advanced-toggle__chevron--open { transform: rotate(180deg); }

.sync-advanced {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-label {
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-secondary);
}

.form-input {
  padding: 6px 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  background: var(--bg-primary);
  font-size: var(--font-size-sm);
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.15s;
}
.form-input:focus { border-color: var(--color-primary); }
.form-select { cursor: pointer; }

/* ── Sync modal ───────────────────────────────────── */
.sync-modal {
  max-width: 520px;
}

.commits-section {
  margin-top: 4px;
}

.commits-panel {
  margin-top: 10px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  overflow: hidden;
}

.commits-loading,
.commits-error,
.commits-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  font-size: var(--font-size-sm);
  color: var(--text-muted);
}

.commits-error { color: #ef4444; }

.commits-list {
  max-height: 220px;
  overflow-y: auto;
}

.commit-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: baseline;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-color);
  font-size: var(--font-size-xs);
}
.commit-item:last-child { border-bottom: none; }

.commit-hash {
  font-family: var(--font-mono);
  color: var(--color-primary);
  font-size: 11px;
  flex-shrink: 0;
}

.commit-message {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.commit-meta {
  color: var(--text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.spinner--sm {
  width: 14px;
  height: 14px;
  border-width: 2px;
}
</style>
