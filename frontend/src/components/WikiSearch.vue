<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { useWikiStore } from '@/stores/wiki'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  navigate: [sectionId: string, pageId: string, keyword: string]
}>()

const wikiStore = useWikiStore()
const searchQuery = ref('')
const selectedIndex = ref(0)
const inputRef = ref<HTMLInputElement | null>(null)
const resultsRef = ref<HTMLElement | null>(null)

interface SearchResult {
  sectionId: string
  sectionTitle: string
  pageId: string
  pageTitle: string
  snippet: string
  isTitle: boolean
}

/** Strip common markdown syntax so snippets read cleanly */
function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, '[代码块]')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/#{1,6}\s+/g, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*>\s+/gm, '')
    .replace(/\n{3,}/g, '\n\n')
}

/** Extract a readable snippet around a match position */
function extractSnippet(text: string, pos: number, queryLen: number): string {
  const before = 55
  const after = 90
  const start = Math.max(0, pos - before)
  const end = Math.min(text.length, pos + queryLen + after)
  const prefix = start > 0 ? '…' : ''
  const suffix = end < text.length ? '…' : ''
  return prefix + text.slice(start, end).replace(/\n/g, ' ').trim() + suffix
}

/** Highlight parts of a snippet for rendering */
function highlightSnippet(snippet: string, query: string): { text: string; hl: boolean }[] {
  if (!query) return [{ text: snippet, hl: false }]
  const parts: { text: string; hl: boolean }[] = []
  const lower = snippet.toLowerCase()
  const qLower = query.toLowerCase()
  let idx = 0
  while (idx < snippet.length) {
    const pos = lower.indexOf(qLower, idx)
    if (pos === -1) {
      parts.push({ text: snippet.slice(idx), hl: false })
      break
    }
    if (pos > idx) parts.push({ text: snippet.slice(idx, pos), hl: false })
    parts.push({ text: snippet.slice(pos, pos + query.length), hl: true })
    idx = pos + query.length
  }
  return parts
}

const results = computed<SearchResult[]>(() => {
  const q = searchQuery.value.trim()
  if (!q || !wikiStore.wiki) return []
  const qLower = q.toLowerCase()
  const found: SearchResult[] = []

  for (const section of wikiStore.wiki.sections) {
    for (const page of section.pages) {
      // Search page title first
      const titleLower = page.title.toLowerCase()
      if (titleLower.includes(qLower)) {
        found.push({
          sectionId: section.id,
          sectionTitle: section.title,
          pageId: page.id,
          pageTitle: page.title,
          snippet: page.title,
          isTitle: true,
        })
      }

      // Search in content_md (stripped)
      const stripped = stripMarkdown(page.content_md || '')
      const strippedLower = stripped.toLowerCase()
      let searchIdx = 0
      let matchCount = 0
      while (matchCount < 2) {
        const pos = strippedLower.indexOf(qLower, searchIdx)
        if (pos === -1) break
        found.push({
          sectionId: section.id,
          sectionTitle: section.title,
          pageId: page.id,
          pageTitle: page.title,
          snippet: extractSnippet(stripped, pos, q.length),
          isTitle: false,
        })
        searchIdx = pos + q.length
        matchCount++
      }

      if (found.length >= 25) break
    }
    if (found.length >= 25) break
  }

  return found.slice(0, 25)
})

function close() {
  emit('update:modelValue', false)
}

function selectResult(result: SearchResult) {
  emit('navigate', result.sectionId, result.pageId, searchQuery.value.trim())
  close()
}

function scrollSelectedIntoView() {
  nextTick(() => {
    const el = resultsRef.value?.querySelector(`[data-idx="${selectedIndex.value}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') { close(); return }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, results.value.length - 1)
    scrollSelectedIntoView()
    return
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
    scrollSelectedIntoView()
    return
  }
  if (e.key === 'Enter') {
    const r = results.value[selectedIndex.value]
    if (r) selectResult(r)
    return
  }
}

watch(() => props.modelValue, (open) => {
  if (open) {
    searchQuery.value = ''
    selectedIndex.value = 0
    nextTick(() => inputRef.value?.focus())
  }
})

watch(searchQuery, () => { selectedIndex.value = 0 })
</script>

<template>
  <Teleport to="body">
    <Transition name="search-fade">
      <div v-if="modelValue" class="search-overlay" @click.self="close" @keydown="handleKeydown">
        <div class="search-modal" role="dialog" aria-modal="true" aria-label="Wiki 搜索">
          <!-- Input -->
          <div class="search-input-wrap">
            <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              ref="inputRef"
              v-model="searchQuery"
              class="search-input"
              type="text"
              placeholder="搜索 Wiki 内容..."
              autocomplete="off"
              spellcheck="false"
            />
            <kbd class="search-esc" @click="close">Esc</kbd>
          </div>

          <!-- Results -->
          <div v-if="results.length > 0" class="search-results" ref="resultsRef">
            <div
              v-for="(result, i) in results"
              :key="`${result.pageId}-${i}`"
              class="search-result"
              :class="{ 'search-result--selected': selectedIndex === i }"
              :data-idx="i"
              @click="selectResult(result)"
              @mouseenter="selectedIndex = i"
            >
              <div class="result-meta">
                <svg v-if="result.isTitle" class="result-icon result-icon--page" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <svg v-else class="result-icon result-icon--text" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="17" y1="10" x2="3" y2="10"/>
                  <line x1="21" y1="6" x2="3" y2="6"/>
                  <line x1="21" y1="14" x2="3" y2="14"/>
                  <line x1="17" y1="18" x2="3" y2="18"/>
                </svg>
                <span class="result-breadcrumb">
                  <span class="breadcrumb-section">{{ result.sectionTitle }}</span>
                  <span class="breadcrumb-sep">›</span>
                  <span class="breadcrumb-page">{{ result.pageTitle }}</span>
                </span>
              </div>
              <div class="result-snippet">
                <template v-for="(part, j) in highlightSnippet(result.snippet, searchQuery.trim())" :key="j">
                  <mark v-if="part.hl" class="result-mark">{{ part.text }}</mark>
                  <span v-else>{{ part.text }}</span>
                </template>
              </div>
            </div>
          </div>

          <!-- Empty state -->
          <div v-else-if="searchQuery.trim()" class="search-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <span>未找到与「{{ searchQuery.trim() }}」匹配的内容</span>
          </div>

          <!-- Hint -->
          <div v-else class="search-hint">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <span>输入关键字全文搜索 Wiki 内容</span>
          </div>

          <!-- Footer hints -->
          <div class="search-footer">
            <span class="footer-hint"><kbd>↑</kbd><kbd>↓</kbd> 导航</span>
            <span class="footer-hint"><kbd>Enter</kbd> 跳转</span>
            <span class="footer-hint"><kbd>Esc</kbd> 关闭</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Overlay */
.search-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 10vh;
  padding-inline: 16px;
}

/* Modal card */
.search-modal {
  width: 100%;
  max-width: 600px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18), 0 8px 24px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  max-height: 70vh;
}

[data-theme="dark"] .search-modal {
  background: #161616;
  border-color: #2a2a2a;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55), 0 8px 24px rgba(0, 0, 0, 0.4);
}

/* Input row */
.search-input-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.search-icon {
  width: 18px;
  height: 18px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 15px;
  color: var(--text-primary);
  font-family: inherit;
  line-height: 1.4;
}

.search-input::placeholder {
  color: var(--text-muted);
}

.search-esc {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 5px;
  padding: 2px 6px;
  cursor: pointer;
  flex-shrink: 0;
  font-family: var(--font-mono);
  transition: background 0.15s;
}

.search-esc:hover {
  background: var(--bg-hover);
}

/* Results list */
.search-results {
  overflow-y: auto;
  flex: 1;
  padding: 6px;
}

.search-result {
  padding: 9px 11px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.1s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.search-result:hover,
.search-result--selected {
  background: var(--bg-hover);
}

[data-theme="dark"] .search-result--selected {
  background: #1e2030;
}

/* Result meta row */
.result-meta {
  display: flex;
  align-items: center;
  gap: 7px;
}

.result-icon {
  width: 13px;
  height: 13px;
  flex-shrink: 0;
}

.result-icon--page {
  color: var(--color-primary);
}

.result-icon--text {
  color: var(--text-muted);
}

.result-breadcrumb {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  overflow: hidden;
}

.breadcrumb-section {
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 150px;
}

.breadcrumb-sep {
  color: var(--text-muted);
  flex-shrink: 0;
}

.breadcrumb-page {
  color: var(--text-secondary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Snippet text */
.result-snippet {
  font-size: 13px;
  color: var(--text-tertiary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.result-mark {
  background: rgba(234, 179, 8, 0.3);
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
  font-weight: 600;
}

[data-theme="dark"] .result-mark {
  background: rgba(253, 224, 71, 0.22);
  color: #fde047;
}

/* Empty / hint states */
.search-empty,
.search-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 32px 20px;
  font-size: 13px;
  color: var(--text-muted);
  justify-content: center;
}

.search-empty svg,
.search-hint svg {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  opacity: 0.5;
}

/* Footer */
.search-footer {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}

.footer-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-muted);
}

kbd {
  font-size: 10px;
  font-family: var(--font-mono);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 1px 5px;
  color: var(--text-muted);
  line-height: 1.6;
}

/* Transition */
.search-fade-enter-active,
.search-fade-leave-active {
  transition: opacity 0.18s ease;
}

.search-fade-enter-active .search-modal,
.search-fade-leave-active .search-modal {
  transition: transform 0.18s ease, opacity 0.18s ease;
}

.search-fade-enter-from,
.search-fade-leave-to {
  opacity: 0;
}

.search-fade-enter-from .search-modal,
.search-fade-leave-to .search-modal {
  transform: translateY(-8px);
  opacity: 0;
}
</style>
