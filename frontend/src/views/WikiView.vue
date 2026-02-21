<script setup lang="ts">
import { onMounted, watch, ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { getWiki, regenerateWiki, deleteWiki } from '@/api/wiki'
import { deleteRepository } from '@/api/repositories'
import { useWikiStore } from '@/stores/wiki'
import { useTaskStore } from '@/stores/task'
import { useEventSource } from '@/composables/useEventSource'
import WikiSidebar from '@/components/WikiSidebar.vue'
import MarkdownView from '@/components/MarkdownView.vue'

const props = defineProps<{ repoId: string }>()
const router = useRouter()
const wikiStore = useWikiStore()
const taskStore = useTaskStore()
const { connectSSE } = useEventSource()

const isLoading = ref(false)
const error = ref('')
const isRegenerating = ref(false)
const showDeleteConfirm = ref(false)
const showDeleteRepoConfirm = ref(false)

// 提取 TOC（从内容中的 h1-h3 标题提取）
const tocItems = computed(() => {
  const content = wikiStore.activePage?.content_md || ''
  const matches = [...content.matchAll(/^(#{1,3})\s+(.+)$/gm)]
  return matches.map((m, i) => ({
    id: `toc-${i}`,
    level: m[1].length,
    text: m[2],
  }))
})

async function loadWiki() {
  isLoading.value = true
  error.value = ''
  try {
    const data = await getWiki(props.repoId)
    wikiStore.setWiki(data)
  } catch (err: unknown) {
    const e = err as { response?: { status?: number } }
    if (e.response?.status === 404) {
      error.value = 'Wiki 尚未生成，请先提交仓库处理任务'
    } else {
      error.value = '加载 Wiki 失败，请检查后端服务'
    }
  } finally {
    isLoading.value = false
  }
}

async function handleRegenerate() {
  isRegenerating.value = true
  try {
    const result = await regenerateWiki(props.repoId)
    taskStore.setTask({
      id: result.task_id,
      repoId: props.repoId,
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
    // 跳回首页查看进度
    router.push({ path: '/', query: { taskId: result.task_id } })
  } catch (err: unknown) {
    const e = err as { response?: { status?: number; data?: { detail?: string } } }
    error.value = e.response?.data?.detail || '重新生成失败'
  } finally {
    isRegenerating.value = false
  }
}

async function handleDeleteWiki() {
  showDeleteConfirm.value = false
  try {
    await deleteWiki(props.repoId)
    wikiStore.clearWiki()
    error.value = 'Wiki 已删除，可以重新生成'
  } catch {
    error.value = '删除 Wiki 失败'
  }
}

async function handleDeleteRepo() {
  showDeleteRepoConfirm.value = false
  try {
    await deleteRepository(props.repoId)
    router.push({ name: 'repos' })
  } catch {
    error.value = '删除仓库失败'
  }
}

onMounted(loadWiki)
watch(() => props.repoId, loadWiki)

// 内容区滚动到顶部
watch(() => wikiStore.activePageId, () => {
  nextTick(() => {
    const el = document.querySelector('.wiki-content-body')
    if (el) el.scrollTop = 0
  })
})
</script>

<template>
  <div class="wiki-view">
    <!-- 侧边栏 -->
    <WikiSidebar v-if="wikiStore.wiki" />

    <!-- 主内容区 -->
    <div class="wiki-main" :class="{ 'wiki-main--no-sidebar': !wikiStore.wiki }">
      <!-- 加载中 -->
      <div v-if="isLoading" class="wiki-loading">
        <span class="spinner" style="width:32px;height:32px;" />
        <span>加载 Wiki...</span>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="error && !wikiStore.wiki" class="wiki-error">
        <div class="alert alert-error">{{ error }}</div>
        <div class="wiki-error-actions">
          <RouterLink :to="{ name: 'home' }" class="btn btn-primary">
            返回首页
          </RouterLink>
          <button class="btn btn-secondary" @click="handleRegenerate" :disabled="isRegenerating">
            重新生成 Wiki
          </button>
        </div>
      </div>

      <!-- Wiki 内容 -->
      <template v-else-if="wikiStore.wiki && wikiStore.activePage">
        <!-- 顶部操作栏 -->
        <div class="wiki-toolbar">
          <div class="wiki-breadcrumb">
            <span class="breadcrumb-repo">{{ wikiStore.wiki.title }}</span>
            <span class="breadcrumb-sep">›</span>
            <span class="breadcrumb-section">{{ wikiStore.activeSection?.title }}</span>
            <span class="breadcrumb-sep">›</span>
            <span class="breadcrumb-page">{{ wikiStore.activePage.title }}</span>
          </div>
          <div class="wiki-actions">
            <RouterLink
              :to="{ name: 'chat', params: { repoId: props.repoId } }"
              class="btn btn-secondary btn-sm"
            >
              💬 AI 问答
            </RouterLink>
            <button
              class="btn btn-secondary btn-sm"
              @click="handleRegenerate"
              :disabled="isRegenerating"
            >
              <span v-if="isRegenerating">生成中...</span>
              <span v-else>🔄 重新生成</span>
            </button>
            <div class="dropdown-group">
              <button class="btn btn-ghost btn-sm" @click="showDeleteConfirm = true">
                🗑 删除 Wiki
              </button>
              <button class="btn btn-ghost btn-sm" style="color:#ef4444" @click="showDeleteRepoConfirm = true">
                ⚠ 删除仓库
              </button>
            </div>
          </div>
        </div>

        <!-- 内容 + TOC 两栏 -->
        <div class="wiki-body-wrap">
          <!-- Markdown 内容 -->
          <div class="wiki-content-body">
            <!-- 相关文件 -->
            <div v-if="wikiStore.activePage.relevant_files?.length" class="relevant-files">
              <details>
                <summary>📁 相关源文件 ({{ wikiStore.activePage.relevant_files.length }})</summary>
                <div class="relevant-files__list">
                  <code
                    v-for="file in wikiStore.activePage.relevant_files"
                    :key="file"
                    class="file-chip"
                  >{{ file }}</code>
                </div>
              </details>
            </div>

            <MarkdownView :content="wikiStore.activePage.content_md" />
          </div>

          <!-- 右侧 TOC -->
          <div class="wiki-toc" v-if="tocItems.length > 0">
            <div class="toc__title">本页目录</div>
            <nav class="toc__nav">
              <a
                v-for="item in tocItems"
                :key="item.id"
                class="toc__item"
                :class="`toc__item--h${item.level}`"
                href="#"
                @click.prevent
              >{{ item.text }}</a>
            </nav>
          </div>
        </div>
      </template>
    </div>

    <!-- 删除 Wiki 确认 -->
    <div v-if="showDeleteConfirm" class="modal-overlay" @click.self="showDeleteConfirm = false">
      <div class="modal card">
        <h3>确认删除 Wiki</h3>
        <p>此操作将删除所有 Wiki 章节和页面，但保留仓库和向量数据，可以重新生成。</p>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="showDeleteConfirm = false">取消</button>
          <button class="btn btn-danger" @click="handleDeleteWiki">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 删除仓库确认 -->
    <div v-if="showDeleteRepoConfirm" class="modal-overlay" @click.self="showDeleteRepoConfirm = false">
      <div class="modal card">
        <h3>⚠ 确认删除仓库</h3>
        <p>此操作将删除仓库、所有 Wiki 内容、向量数据和本地克隆，<strong>不可恢复</strong>。</p>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="showDeleteRepoConfirm = false">取消</button>
          <button class="btn btn-danger" @click="handleDeleteRepo">确认删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wiki-view {
  display: flex;
  flex: 1;
  height: calc(100vh - var(--header-height));
  overflow: hidden;
}

.wiki-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.wiki-main--no-sidebar {
  max-width: 900px;
  margin: 0 auto;
  padding: 24px;
  width: 100%;
}

.wiki-loading, .wiki-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 60px 24px;
  color: var(--text-muted);
}

.wiki-error { align-items: flex-start; max-width: 600px; margin: 40px auto; }
.wiki-error-actions { display: flex; gap: 10px; }

.wiki-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-primary);
  flex-wrap: wrap;
  gap: 8px;
  flex-shrink: 0;
}

.wiki-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--font-size-sm);
  color: var(--text-muted);
  min-width: 0;
  overflow: hidden;
}

.breadcrumb-repo { color: var(--text-tertiary); white-space: nowrap; }
.breadcrumb-sep { color: var(--text-muted); }
.breadcrumb-section { color: var(--text-secondary); white-space: nowrap; }
.breadcrumb-page {
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wiki-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.dropdown-group { display: flex; gap: 4px; }

.wiki-body-wrap {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.wiki-content-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  min-width: 0;
}

.wiki-toc {
  width: var(--toc-width);
  flex-shrink: 0;
  padding: 24px 16px;
  border-left: 1px solid var(--border-color);
  overflow-y: auto;
  background: var(--bg-secondary);
}

.toc__title {
  font-size: var(--font-size-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.toc__nav { display: flex; flex-direction: column; gap: 4px; }

.toc__item {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  text-decoration: none;
  padding: 2px 0;
  transition: color 0.15s;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toc__item:hover { color: var(--text-primary); text-decoration: none; }
.toc__item--h1 { font-weight: 600; color: var(--text-secondary); }
.toc__item--h2 { padding-left: 12px; }
.toc__item--h3 { padding-left: 24px; }

.relevant-files {
  margin-bottom: 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  overflow: hidden;
}

.relevant-files summary {
  padding: 8px 12px;
  cursor: pointer;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  user-select: none;
}

.relevant-files__list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid var(--border-color);
}

.file-chip {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* 模态框 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.modal {
  max-width: 400px;
  width: 100%;
}

.modal h3 {
  margin-bottom: 12px;
  font-size: var(--font-size-lg);
}

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

@media (max-width: 768px) {
  .wiki-toc { display: none; }
  .wiki-content-body { padding: 16px; }
  .wiki-toolbar { padding: 8px 12px; }
  .dropdown-group { display: none; }
}
</style>
