<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { getRepositories, deleteRepository, reprocessRepository } from '@/api/repositories'
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
const actionLoading = ref<string | null>(null) // repoId

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

async function handleReprocess(repo: RepositoryItem) {
  actionLoading.value = repo.id
  try {
    const result = await reprocessRepository(repo.id)
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

function formatDate(dateStr: string | null) {
  if (!dateStr) return '从未同步'
  return new Date(dateStr).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(loadRepos)
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
        ＋ 添加仓库
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
      <button class="btn btn-ghost btn-sm" @click="loadRepos" :disabled="repoStore.isLoading">
        {{ repoStore.isLoading ? '刷新中...' : '🔄 刷新' }}
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
      <div class="list-empty__icon">📂</div>
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
            </div>
            <h3 class="repo-card__name">{{ repo.name }}</h3>
          </div>
          <StatusBadge :status="repo.status" />
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
            class="btn btn-secondary btn-sm"
            :disabled="actionLoading === repo.id || ['pending', 'cloning', 'parsing', 'embedding', 'generating', 'syncing'].includes(repo.status)"
            @click="handleReprocess(repo)"
          >
            <span v-if="actionLoading === repo.id">处理中...</span>
            <span v-else>重新处理</span>
          </button>
          <button
            class="btn btn-ghost btn-sm"
            style="color:#ef4444"
            :disabled="actionLoading === repo.id"
            @click="deleteTarget = repo"
          >
            删除
          </button>
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
  </div>
</template>

<style scoped>
.repo-list-view {
  max-width: 1100px;
  margin: 0 auto;
  padding: 32px 20px;
  width: 100%;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}

.page-title {
  font-size: var(--font-size-3xl);
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.page-desc {
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
}

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
  padding: 6px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-full);
  background: var(--bg-primary);
  font-size: var(--font-size-xs);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}
.filter-btn:hover { background: var(--bg-hover); }
.filter-btn--active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}

.list-loading, .list-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 20px;
  color: var(--text-muted);
}

.list-empty__icon { font-size: 48px; }
.list-empty h3 { font-size: var(--font-size-xl); color: var(--text-secondary); }
.list-empty p { font-size: var(--font-size-sm); color: var(--text-muted); }

.repo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.repo-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: box-shadow 0.2s;
}

.repo-card:hover { box-shadow: var(--shadow-md); }

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

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
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

.modal h3 { margin-bottom: 12px; font-size: var(--font-size-lg); }
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

@media (max-width: 640px) {
  .repo-grid { grid-template-columns: 1fr; }
  .page-header { flex-direction: column; gap: 16px; }
}
</style>
