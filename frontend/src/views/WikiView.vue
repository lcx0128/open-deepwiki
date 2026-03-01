<script setup lang="ts">
import { onMounted, onUnmounted, watch, ref, computed, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { getWiki, regenerateWiki, deleteWiki } from '@/api/wiki'
import { deleteRepository } from '@/api/repositories'
import { useWikiStore } from '@/stores/wiki'
import { useTaskStore } from '@/stores/task'
import { useRepoStore } from '@/stores/repo'
import { useEventSource } from '@/composables/useEventSource'
import WikiSidebar from '@/components/WikiSidebar.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import WikiSearch from '@/components/WikiSearch.vue'
import WikiRegenerateDialog from '@/components/WikiRegenerateDialog.vue'

const props = defineProps<{ repoId: string }>()
const router = useRouter()
const wikiStore = useWikiStore()
const taskStore = useTaskStore()
const repoStore = useRepoStore()
const { connectSSE } = useEventSource()

const currentRepo = computed(() => repoStore.repos.find(r => r.id === props.repoId))

const canRegenerate = computed(() => {
  if (currentRepo.value) {
    const { status, failed_at_stage } = currentRepo.value
    if (status === 'error' && failed_at_stage && failed_at_stage !== 'generating') {
      return false
    }
  }
  return true
})

function stageLabel(stage: string | null | undefined): string {
  const map: Record<string, string> = {
    cloning: '克隆',
    parsing: '代码解析',
    embedding: '向量化',
    generating: 'Wiki生成',
  }
  return stage ? (map[stage] || stage) : '未知'
}

const isLoading = ref(false)
const error = ref('')
const isRegenerating = ref(false)
const showRegenerateDialog = ref(false)
const showDeleteConfirm = ref(false)
const showDeleteRepoConfirm = ref(false)
const chatQuery = ref('')
const isSidebarOpen = ref(false)
const deepResearchMode = ref(false)
const showSearch = ref(false)
const pendingSearchKeyword = ref('')

function handleGlobalKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault()
    if (wikiStore.wiki) showSearch.value = true
  }
}

/** Walk the rendered wiki content and scroll to the first text node matching keyword */
async function scrollToKeyword(keyword: string) {
  if (!keyword) return
  // Wait for MarkdownView to finish rendering
  await nextTick()
  await new Promise<void>(resolve => setTimeout(resolve, 250))

  const container = document.querySelector('.wiki-content-body')
  if (!container) return

  const keywordLower = keyword.toLowerCase()
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)

  let node: Node | null
  while ((node = walker.nextNode())) {
    const text = node.textContent ?? ''
    if (text.toLowerCase().includes(keywordLower)) {
      const el = node.parentElement
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('wiki-search-highlight')
        setTimeout(() => el.classList.remove('wiki-search-highlight'), 2200)
      }
      break
    }
  }
}

async function handleSearchNavigate(sectionId: string, pageId: string, keyword: string) {
  if (wikiStore.activePageId === pageId) {
    // Already on this page — watcher won't fire, scroll directly
    scrollToKeyword(keyword)
  } else {
    pendingSearchKeyword.value = keyword
    wikiStore.setActivePage(sectionId, pageId)
  }
}

// TOC from page headings
const tocItems = computed(() => {
  const content = wikiStore.activePage?.content_md || ''
  const matches = [...content.matchAll(/^(#{1,3})\s+(.+)$/gm)]
  return matches.map((m, i) => ({
    id: `toc-${i}`,
    level: m[1].length,
    text: m[2],
  }))
})

function handleTocClick(index: number) {
  const contentBody = document.querySelector('.wiki-content-body')
  if (!contentBody) return
  const headings = contentBody.querySelectorAll('h1, h2, h3, h4, h5, h6')
  if (headings[index]) {
    headings[index].scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

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

function handleRegenerate() {
  showRegenerateDialog.value = true
}

async function handleRegenerateConfirm(payload: { mode: 'full' | 'partial'; pageIds: string[]; llmProvider: string; llmModel: string }) {
  showRegenerateDialog.value = false
  isRegenerating.value = true
  try {
    const requestData: { pages?: string[]; llm_provider?: string; llm_model?: string } = {}
    if (payload.mode === 'partial') requestData.pages = payload.pageIds
    if (payload.llmProvider) requestData.llm_provider = payload.llmProvider
    if (payload.llmModel)    requestData.llm_model    = payload.llmModel
    const result = await regenerateWiki(props.repoId, requestData)
    taskStore.setTask({
      id: result.task_id,
      repoId: props.repoId,
      type: 'wiki_regenerate',
      status: 'pending',
      progressPct: 0,
      currentStage: payload.mode === 'partial' ? `重新生成 ${payload.pageIds.length} 个页面...` : 'Wiki 重新生成已开始...',
      filesTotal: 0,
      filesProcessed: 0,
      errorMsg: null,
      wikiId: null,
    })
    connectSSE(result.task_id)
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

function handleChatSubmit() {
  const q = chatQuery.value.trim()
  if (!q) return
  chatQuery.value = ''
  const query: Record<string, string> = { q }
  if (deepResearchMode.value) query.dr = '1'
  router.push({ name: 'chat', params: { repoId: props.repoId }, query })
}

function handleExportMarkdown() {
  const wiki = wikiStore.wiki
  if (!wiki) return

  const lines: string[] = []

  // Wiki 标题
  lines.push(`# ${wiki.title}`)
  lines.push('')

  // 按 section -> page 顺序组织内容
  for (const section of wiki.sections) {
    lines.push(`## ${section.title}`)
    lines.push('')

    for (const page of section.pages) {
      lines.push(`### ${page.title}`)
      lines.push('')

      // 相关文件列表
      if (page.relevant_files && page.relevant_files.length > 0) {
        lines.push('**相关文件：**')
        for (const file of page.relevant_files) {
          lines.push(`- \`${file}\``)
        }
        lines.push('')
      }

      // 页面内容
      if (page.content_md) {
        lines.push(page.content_md)
        lines.push('')
      }
    }
  }

  const content = lines.join('\n')
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  // 处理文件名中的特殊字符
  const filename = wiki.title.replace(/[<>:"/\\|?*]/g, '_') + '.md'
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function handleChatKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleChatSubmit()
  }
}

onMounted(() => {
  loadWiki()
  window.addEventListener('keydown', handleGlobalKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
})

watch(() => props.repoId, loadWiki)

watch(() => wikiStore.activePageId, async () => {
  await nextTick()
  const el = document.querySelector('.wiki-content-body')
  if (el) el.scrollTop = 0
  isSidebarOpen.value = false
  // Scroll to search keyword if a search navigation was triggered
  if (pendingSearchKeyword.value) {
    const kw = pendingSearchKeyword.value
    pendingSearchKeyword.value = ''
    scrollToKeyword(kw)
  }
})
</script>

<template>
  <div class="wiki-view">
    <!-- Mobile sidebar overlay -->
    <div
      v-if="isSidebarOpen"
      class="sidebar-overlay"
      @click="isSidebarOpen = false"
    />

    <!-- Left sidebar -->
    <WikiSidebar
      v-if="wikiStore.wiki"
      :class="{ 'sidebar--mobile-open': isSidebarOpen }"
    />

    <!-- Main content area -->
    <div class="wiki-main" :class="{ 'wiki-main--no-sidebar': !wikiStore.wiki }">
      <!-- Loading state -->
      <div v-if="isLoading" class="wiki-loading">
        <span class="spinner" style="width:28px;height:28px;" />
        <span>加载中...</span>
      </div>

      <!-- Error state -->
      <div v-else-if="error && !wikiStore.wiki" class="wiki-error">
        <div class="alert alert-error">{{ error }}</div>
        <div v-if="!canRegenerate" class="wiki-error-hint">
          仓库在 <strong>{{ stageLabel(currentRepo?.failed_at_stage) }}</strong> 阶段失败，向量数据不完整，无法生成 Wiki。请前往仓库列表重新处理该仓库。
        </div>
        <div class="wiki-error-actions">
          <RouterLink :to="{ name: 'home' }" class="btn btn-primary">返回仓库列表</RouterLink>
          <button
            v-if="canRegenerate"
            class="btn btn-secondary"
            @click="handleRegenerate"
            :disabled="isRegenerating"
          >
            重新生成 Wiki
          </button>
        </div>
      </div>

      <!-- Wiki content -->
      <template v-else-if="wikiStore.wiki && wikiStore.activePage">
        <!-- Toolbar -->
        <div class="wiki-toolbar">
          <div class="wiki-toolbar__left">
            <!-- Mobile menu button -->
            <button class="mobile-menu-btn" @click="isSidebarOpen = !isSidebarOpen">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="3" y1="6" x2="21" y2="6"/>
                <line x1="3" y1="12" x2="21" y2="12"/>
                <line x1="3" y1="18" x2="21" y2="18"/>
              </svg>
            </button>
            <nav class="wiki-breadcrumb" aria-label="breadcrumb">
              <span class="breadcrumb-item">{{ wikiStore.wiki.title }}</span>
              <span class="breadcrumb-sep">›</span>
              <span class="breadcrumb-item breadcrumb-item--active">{{ wikiStore.activePage.title }}</span>
            </nav>
          </div>
          <div class="wiki-toolbar__right">
            <button
              class="toolbar-btn toolbar-btn--search"
              @click="showSearch = true"
              title="搜索 Wiki 内容 (Ctrl+K)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
              <span>搜索</span>
              <kbd class="toolbar-kbd">Ctrl K</kbd>
            </button>
            <div class="toolbar-divider" />
            <button
              class="toolbar-btn"
              @click="handleRegenerate"
              :disabled="isRegenerating"
              title="重新生成 Wiki（支持全量或选择性）"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
              </svg>
              <span>重新生成</span>
              <span class="toolbar-btn__hint">全量/部分</span>
            </button>
            <button
              class="toolbar-btn"
              @click="handleExportMarkdown"
              :disabled="!wikiStore.wiki"
              title="导出为 Markdown"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              <span>导出 MD</span>
            </button>
            <div class="toolbar-divider" />
            <button class="toolbar-btn toolbar-btn--danger" @click="showDeleteConfirm = true" title="删除 Wiki">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6l-1 14H6L5 6M10 11v6M14 11v6M9 6V4h6v2"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Content + TOC layout -->
        <div class="wiki-body-wrap">
          <!-- Main content -->
          <div class="wiki-content-body">
            <!-- Relevant files -->
            <div v-if="wikiStore.activePage.relevant_files?.length" class="relevant-files">
              <div class="relevant-files__header">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="relevant-files__icon">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
                <span>相关文件</span>
              </div>
              <div class="relevant-files__list">
                <code
                  v-for="file in wikiStore.activePage.relevant_files"
                  :key="file"
                  class="file-chip"
                >{{ file }}</code>
              </div>
            </div>

            <MarkdownView :content="wikiStore.activePage.content_md" :key="wikiStore.activePageId" />

            <!-- Bottom padding for chat bar -->
            <div style="height: 80px" />
          </div>

          <!-- Right TOC -->
          <div class="wiki-toc" v-if="tocItems.length > 0">
            <div class="toc__title">本页目录</div>
            <nav class="toc__nav">
              <a
                v-for="(item, i) in tocItems"
                :key="item.id"
                class="toc__item"
                :class="`toc__item--h${item.level}`"
                href="#"
                @click.prevent="handleTocClick(i)"
              >{{ item.text }}</a>
            </nav>
          </div>
        </div>

        <!-- Bottom chat bar (fixed) -->
        <div class="wiki-chat-bar">
          <div class="chat-bar__inner">
            <!-- Deep research toggle -->
            <button
              class="chat-bar__dr-toggle"
              :class="{ 'chat-bar__dr-toggle--active': deepResearchMode }"
              @click="deepResearchMode = !deepResearchMode"
              :title="deepResearchMode ? '关闭深度研究' : '开启深度研究 (5轮深度分析)'"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/>
              </svg>
            </button>
            <div class="chat-bar__icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </div>
            <input
              v-model="chatQuery"
              class="chat-bar__input"
              type="text"
              placeholder="向 AI 提问关于这个代码库的问题..."
              @keydown="handleChatKeydown"
            />
            <button
              class="chat-bar__btn"
              @click="handleChatSubmit"
              :disabled="!chatQuery.trim()"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
        </div>
      </template>
    </div>

    <!-- Wiki Search modal -->
    <WikiSearch
      v-model="showSearch"
      @navigate="handleSearchNavigate"
    />

    <!-- Regenerate Wiki dialog -->
    <WikiRegenerateDialog
      v-if="showRegenerateDialog"
      :wiki="wikiStore.wiki"
      v-model:visible="showRegenerateDialog"
      @confirm="handleRegenerateConfirm"
      @cancel="showRegenerateDialog = false"
    />

    <!-- Delete Wiki modal -->
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

    <!-- Delete Repo modal -->
    <div v-if="showDeleteRepoConfirm" class="modal-overlay" @click.self="showDeleteRepoConfirm = false">
      <div class="modal card">
        <h3>确认删除仓库</h3>
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
  position: relative;
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
  padding: 80px 24px;
  color: var(--text-muted);
}

.wiki-error { align-items: flex-start; max-width: 600px; margin: 40px auto; }
.wiki-error-actions { display: flex; gap: 10px; }
.wiki-error-hint {
  margin: 0.75rem 0;
  color: var(--color-text-secondary, #888);
  font-size: 0.875rem;
}

/* Toolbar */
.wiki-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 16px;
  height: 44px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-primary);
  flex-shrink: 0;
}

.wiki-toolbar__left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.wiki-toolbar__right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.mobile-menu-btn {
  display: none;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  border-radius: var(--radius);
  flex-shrink: 0;
}
.mobile-menu-btn svg { width: 18px; height: 18px; }

.wiki-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-muted);
  min-width: 0;
  overflow: hidden;
}

.breadcrumb-item {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.breadcrumb-item--active {
  color: var(--text-primary);
  font-weight: 500;
}

.breadcrumb-sep { color: var(--border-color-strong); flex-shrink: 0; }

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 12px;
  color: var(--text-muted);
  transition: all 0.15s;
  white-space: nowrap;
}

.toolbar-btn__hint {
  font-size: 10px;
  color: var(--text-muted, #94a3b8);
  line-height: 1;
  margin-top: 1px;
}
.toolbar-btn svg { width: 14px; height: 14px; }
.toolbar-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
  border-color: var(--border-color);
}
.toolbar-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.toolbar-btn--danger:hover { color: var(--color-error); }

.toolbar-divider {
  width: 1px;
  height: 20px;
  background: var(--border-color);
  margin: 0 2px;
}

/* Content layout */
.wiki-body-wrap {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.wiki-content-body {
  flex: 1;
  overflow-y: auto;
  padding: 28px 40px;
  min-width: 0;
}

/* TOC */
.wiki-toc {
  width: var(--toc-width);
  flex-shrink: 0;
  padding: 24px 16px;
  border-left: 1px solid var(--border-color);
  overflow-y: auto;
}

.toc__title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  margin-bottom: 10px;
}

.toc__nav { display: flex; flex-direction: column; gap: 2px; }

.toc__item {
  font-size: 13px;
  color: var(--text-tertiary);
  text-decoration: none;
  padding: 4px 0;
  transition: color 0.15s;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.toc__item:hover { color: var(--text-primary); text-decoration: none; }
.toc__item--h1 { font-weight: 600; color: var(--text-secondary); }
.toc__item--h2 { padding-left: 12px; }
.toc__item--h3 { padding-left: 24px; font-size: 12px; }

/* Relevant files */
.relevant-files {
  margin-bottom: 20px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  overflow: hidden;
}

.relevant-files__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 500;
}

.relevant-files__icon {
  width: 13px;
  height: 13px;
}

.relevant-files__list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding: 8px 12px 10px;
  border-top: 1px solid var(--border-color);
}

.file-chip {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 2px 7px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

/* Bottom chat bar */
.wiki-chat-bar {
  position: absolute;
  bottom: 0;
  left: var(--sidebar-width);
  right: 0;
  padding: 20px 24px 24px;
  background: transparent;
  z-index: 10;
  display: flex;
  justify-content: center;
  pointer-events: none;
}

.chat-bar__inner {
  max-width: 680px;
  width: 100%;
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.55);
  border-radius: var(--radius-xl);
  padding: 8px 8px 8px 14px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1), 0 2px 8px rgba(0, 0, 0, 0.06);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.chat-bar__inner:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12), 0 0 0 3px var(--color-primary-light);
}

[data-theme="dark"] .chat-bar__inner {
  background: rgba(17, 17, 17, 0.8);
  border-color: rgba(255, 255, 255, 0.08);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5), 0 2px 8px rgba(0, 0, 0, 0.35);
}

.chat-bar__icon {
  color: var(--text-muted);
  flex-shrink: 0;
}
.chat-bar__icon svg { width: 16px; height: 16px; display: block; }

.chat-bar__input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 14px;
  color: var(--text-primary);
  font-family: inherit;
}

.chat-bar__input::placeholder { color: var(--text-muted); }

.chat-bar__btn {
  width: 34px;
  height: 34px;
  background: var(--color-primary);
  border: none;
  border-radius: var(--radius-lg);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  transition: background 0.15s;
  flex-shrink: 0;
}
.chat-bar__btn svg { width: 15px; height: 15px; }
.chat-bar__btn:hover:not(:disabled) { background: var(--color-primary-dark); }
.chat-bar__btn:disabled { opacity: 0.35; cursor: not-allowed; }

/* When no sidebar, chat bar spans full width */
.wiki-main--no-sidebar .wiki-chat-bar {
  left: 0;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
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
.modal p { font-size: 13px; color: var(--text-secondary); margin-bottom: 20px; line-height: 1.6; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }

/* Sidebar overlay for mobile */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 199;
}

@media (max-width: 768px) {
  .wiki-toc { display: none; }
  .wiki-content-body { padding: 16px 20px; }
  .mobile-menu-btn { display: flex; }
  .wiki-chat-bar { left: 0; padding: 12px 16px 20px; }
  .wiki-toolbar { padding: 0 12px; }
  :deep(.sidebar--mobile-open) {
    transform: translateX(0) !important;
  }
}

.chat-bar__dr-toggle {
  width: 30px;
  height: 30px;
  border-radius: var(--radius);
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  transition: all 0.15s;
  flex-shrink: 0;
}

.chat-bar__dr-toggle svg {
  width: 14px;
  height: 14px;
}

.chat-bar__dr-toggle:hover {
  border-color: #7c3aed;
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.08);
}

.chat-bar__dr-toggle--active {
  border-color: #7c3aed;
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.15);
}

/* Toolbar search button */
.toolbar-btn--search {
  gap: 6px;
}

.toolbar-kbd {
  font-size: 10px;
  font-family: var(--font-mono);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 1px 5px;
  color: var(--text-muted);
  line-height: 1.6;
  letter-spacing: 0.02em;
}

@media (max-width: 900px) {
  .toolbar-kbd { display: none; }
}

/* Keyword scroll-to highlight flash */
:deep(.wiki-search-highlight) {
  border-radius: 3px;
  outline-offset: 1px;
  animation: wiki-kw-flash 2s ease-out forwards;
}

@keyframes wiki-kw-flash {
  0%   { background: rgba(253, 224, 71, 0.55); outline: 2px solid rgba(234, 179, 8, 0.6); }
  40%  { background: rgba(253, 224, 71, 0.4);  outline: 2px solid rgba(234, 179, 8, 0.4); }
  100% { background: transparent;               outline: 2px solid transparent; }
}
</style>
