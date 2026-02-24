<script setup lang="ts">
import { ref, computed } from 'vue'
import { useWikiStore } from '@/stores/wiki'

const wikiStore = useWikiStore()
const isMobileOpen = ref(false)
const expandedSections = ref<Set<string>>(new Set())

const sections = computed(() => wikiStore.wiki?.sections || [])

function toggleSection(sectionId: string) {
  if (expandedSections.value.has(sectionId)) {
    expandedSections.value.delete(sectionId)
  } else {
    expandedSections.value.add(sectionId)
  }
}

function selectPage(sectionId: string, pageId: string) {
  wikiStore.setActivePage(sectionId, pageId)
  isMobileOpen.value = false
}

function isActiveSection(sectionId: string) {
  return wikiStore.activeSectionId === sectionId
}

function isActivePage(pageId: string) {
  return wikiStore.activePageId === pageId
}

// 初始化展开当前激活的 section
function isSectionVisible(sectionId: string) {
  return expandedSections.value.has(sectionId) || isActiveSection(sectionId)
}

const importanceLabel: Record<string, { icon: string; color: string }> = {
  high: { icon: '●', color: '#ef4444' },
  medium: { icon: '●', color: '#f59e0b' },
  low: { icon: '●', color: '#10b981' },
}
</script>

<template>
  <!-- 移动端切换按钮 -->
  <button
    class="mobile-toggle btn btn-secondary btn-sm"
    @click="isMobileOpen = !isMobileOpen"
    aria-label="切换目录"
  >
    <span>{{ isMobileOpen ? '✕' : '☰' }}</span>
    目录
  </button>

  <!-- 侧边栏 -->
  <aside class="sidebar" :class="{ 'sidebar--open': isMobileOpen }">
    <!-- Wiki 标题 -->
    <div class="sidebar__header">
      <h2 class="sidebar__title">{{ wikiStore.wiki?.title || '文档' }}</h2>
      <div class="sidebar__meta">
        {{ wikiStore.totalPages }} 个页面
      </div>
    </div>

    <!-- 导航树 -->
    <nav class="sidebar__nav" role="navigation">
      <div
        v-for="section in sections"
        :key="section.id"
        class="sidebar__section"
      >
        <!-- Section 标题（可折叠） -->
        <button
          class="sidebar__section-header"
          :class="{ 'sidebar__section-header--active': isActiveSection(section.id) }"
          @click="toggleSection(section.id)"
          :aria-expanded="isSectionVisible(section.id)"
        >
          <span class="section__chevron">
            {{ isSectionVisible(section.id) ? '▾' : '▸' }}
          </span>
          <span class="section__title">{{ section.title }}</span>
          <span class="section__count">{{ section.pages.length }}</span>
        </button>

        <!-- Page 列表 -->
        <ul v-if="isSectionVisible(section.id)" class="sidebar__pages">
          <li
            v-for="page in section.pages"
            :key="page.id"
            class="sidebar__page"
            :class="{ 'sidebar__page--active': isActivePage(page.id) }"
            @click="selectPage(section.id, page.id)"
            role="button"
            :tabindex="0"
            @keydown.enter="selectPage(section.id, page.id)"
          >
            <span
              class="page__importance"
              :style="{ color: importanceLabel[page.importance]?.color }"
              :title="`重要性: ${page.importance}`"
            >
              {{ importanceLabel[page.importance]?.icon }}
            </span>
            <span class="page__title">{{ page.title }}</span>
          </li>
        </ul>
      </div>
    </nav>
  </aside>

  <!-- 移动端遮罩 -->
  <div
    v-if="isMobileOpen"
    class="sidebar-overlay"
    @click="isMobileOpen = false"
  />
</template>

<style scoped>
.mobile-toggle {
  display: none;
  position: fixed;
  top: calc(var(--header-height) + 12px);
  left: 12px;
  z-index: 200;
}

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
  padding: 16px;
  border-bottom: 1px solid var(--border-color);
}

.sidebar__title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar__meta {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
}

.sidebar__nav {
  flex: 1;
  padding: 8px 0;
  overflow-y: auto;
}

.sidebar__section {
  margin-bottom: 2px;
}

.sidebar__section-header {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: var(--font-size-sm);
  font-weight: 500;
  transition: background 0.15s;
  border-radius: 0;
}

.sidebar__section-header:hover {
  background: var(--bg-hover);
}

.sidebar__section-header--active {
  color: var(--color-primary);
}

.section__chevron {
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.section__title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section__count {
  font-size: var(--font-size-xs);
  color: var(--text-muted);
  background: var(--bg-tertiary);
  padding: 1px 6px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

.sidebar__pages {
  list-style: none;
  padding: 0;
  padding-left: 8px;
}

.sidebar__page {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px 6px 24px;
  cursor: pointer;
  border-radius: 0;
  font-size: var(--font-size-sm);
  color: var(--text-secondary);
  transition: all 0.15s;
  border-left: 2px solid transparent;
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

.page__importance {
  font-size: 8px;
  flex-shrink: 0;
}

.page__title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 199;
}

/* 移动端 */
@media (max-width: 768px) {
  .mobile-toggle { display: flex; }
  .sidebar {
    position: fixed;
    top: var(--header-height);
    left: 0;
    z-index: 200;
    transform: translateX(-100%);
    transition: transform 0.3s ease;
    box-shadow: var(--shadow-lg);
  }
  .sidebar--open { transform: translateX(0); }
  .sidebar-overlay { display: block; }
}
</style>
