<script setup lang="ts">
import { computed } from 'vue'
import { useWikiStore } from '@/stores/wiki'

const wikiStore = useWikiStore()

const sections = computed(() => wikiStore.wiki?.sections || [])

function selectPage(sectionId: string, pageId: string) {
  wikiStore.setActivePage(sectionId, pageId)
}

function isActivePage(pageId: string) {
  return wikiStore.activePageId === pageId
}

const importanceColor: Record<string, string> = {
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#10b981',
}
</script>

<template>
  <aside class="sidebar">
    <!-- Wiki title -->
    <div class="sidebar__header">
      <h2 class="sidebar__title">{{ wikiStore.wiki?.title || '文档' }}</h2>
      <span class="sidebar__count">{{ wikiStore.totalPages }} 页</span>
    </div>

    <!-- Navigation tree - always expanded -->
    <nav class="sidebar__nav" role="navigation" aria-label="Wiki 目录">
      <div
        v-for="section in sections"
        :key="section.id"
        class="sidebar__section"
      >
        <!-- Section header (not clickable, just a label) -->
        <div class="sidebar__section-label">
          {{ section.title }}
        </div>

        <!-- Pages (always visible, no toggle) -->
        <ul class="sidebar__pages">
          <li
            v-for="page in section.pages"
            :key="page.id"
            class="sidebar__page"
            :class="{ 'sidebar__page--active': isActivePage(page.id) }"
            @click="selectPage(section.id, page.id)"
            role="button"
            tabindex="0"
            @keydown.enter="selectPage(section.id, page.id)"
          >
            <span
              class="page__dot"
              :style="{ background: importanceColor[page.importance] || '#94a3b8' }"
            />
            <span class="page__title">{{ page.title }}</span>
          </li>
        </ul>
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: calc(100vh - var(--header-height));
  overflow-y: auto;
  border-right: 1px solid var(--border-color);
  background: var(--bg-secondary);
  position: sticky;
  top: var(--header-height);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.sidebar__header {
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-shrink: 0;
}

.sidebar__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  letter-spacing: -0.01em;
}

.sidebar__count {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  flex-shrink: 0;
}

.sidebar__nav {
  flex: 1;
  padding: 8px 0 24px;
  overflow-y: auto;
}

.sidebar__section {
  margin-bottom: 4px;
}

.sidebar__section-label {
  padding: 10px 16px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  user-select: none;
}

.sidebar__pages {
  list-style: none;
  padding: 0;
}

.sidebar__page {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px 6px 20px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-secondary);
  transition: all 0.1s;
  border-left: 2px solid transparent;
  user-select: none;
  position: relative;
}

.sidebar__page:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.sidebar__page--active {
  background: var(--bg-active);
  color: var(--color-primary);
  font-weight: 500;
  border-left-color: var(--color-primary);
}

.page__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.7;
}

.sidebar__page--active .page__dot {
  opacity: 1;
}

.page__title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}

/* Mobile: sidebar as overlay */
@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    top: var(--header-height);
    left: 0;
    z-index: 200;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    box-shadow: var(--shadow-lg);
    height: calc(100vh - var(--header-height));
  }
}
</style>
