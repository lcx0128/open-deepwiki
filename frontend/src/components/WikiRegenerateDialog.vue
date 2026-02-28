<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { WikiResponse } from '@/api/wiki'

const props = defineProps<{
  wiki: WikiResponse | null
  visible: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  confirm: [payload: { mode: 'full' | 'partial'; pageIds: string[] }]
  cancel: []
}>()

function hasEmptyPages(): boolean {
  if (!props.wiki) return false
  for (const section of props.wiki.sections) {
    for (const page of section.pages) {
      if (!page.content_md || page.content_md.trim() === '') return true
    }
  }
  return false
}

const mode = ref<'full' | 'partial'>(hasEmptyPages() ? 'partial' : 'full')
const selectedPageIds = ref<Set<string>>(new Set())

function isPageEmpty(contentMd: string): boolean {
  return !contentMd || contentMd.trim() === ''
}

function initSelection() {
  const initial = new Set<string>()
  if (!props.wiki) return initial
  for (const section of props.wiki.sections) {
    for (const page of section.pages) {
      if (isPageEmpty(page.content_md)) {
        initial.add(page.id)
      }
    }
  }
  return initial
}

watch(
  () => props.visible,
  (open) => {
    if (open) {
      mode.value = hasEmptyPages() ? 'partial' : 'full'
      selectedPageIds.value = initSelection()
    }
  },
  { immediate: true }
)

function getSectionState(sectionId: string): 'all' | 'some' | 'none' {
  if (!props.wiki) return 'none'
  const section = props.wiki.sections.find(s => s.id === sectionId)
  if (!section || section.pages.length === 0) return 'none'
  const checkedCount = section.pages.filter(p => selectedPageIds.value.has(p.id)).length
  if (checkedCount === section.pages.length) return 'all'
  if (checkedCount > 0) return 'some'
  return 'none'
}

function toggleSection(sectionId: string) {
  if (!props.wiki) return
  const section = props.wiki.sections.find(s => s.id === sectionId)
  if (!section) return
  const state = getSectionState(sectionId)
  const next = new Set(selectedPageIds.value)
  if (state === 'all') {
    for (const page of section.pages) next.delete(page.id)
  } else {
    for (const page of section.pages) next.add(page.id)
  }
  selectedPageIds.value = next
}

function togglePage(pageId: string) {
  const next = new Set(selectedPageIds.value)
  if (next.has(pageId)) {
    next.delete(pageId)
  } else {
    next.add(pageId)
  }
  selectedPageIds.value = next
}

const canConfirm = computed(() => {
  if (mode.value === 'full') return true
  return selectedPageIds.value.size > 0
})

function handleConfirm() {
  emit('confirm', {
    mode: mode.value,
    pageIds: mode.value === 'partial' ? [...selectedPageIds.value] : [],
  })
}

function handleClose() {
  emit('update:visible', false)
  emit('cancel')
}

function handleBackdrop() {
  handleClose()
}

function setSectionIndeterminate(el: HTMLInputElement | null, sectionId: string) {
  if (!el) return
  const state = getSectionState(sectionId)
  el.indeterminate = state === 'some'
}
</script>

<template>
  <Teleport to="body">
    <Transition name="regen-fade">
      <div v-if="visible" class="regen-overlay" @click.self="handleBackdrop">
        <div class="regen-modal" role="dialog" aria-modal="true" aria-label="重新生成 Wiki">
          <!-- Header -->
          <div class="regen-modal__header">
            <span class="regen-modal__title">重新生成 Wiki</span>
            <button class="regen-modal__close" @click="handleClose" aria-label="关闭">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="regen-modal__body">
            <!-- Mode selection -->
            <div class="regen-modes">
              <label class="regen-mode" :class="{ 'regen-mode--active': mode === 'full' }">
                <input
                  type="radio"
                  name="regen-mode"
                  value="full"
                  v-model="mode"
                  class="regen-mode__radio"
                />
                <div class="regen-mode__content">
                  <span class="regen-mode__label">全量重新生成</span>
                  <span class="regen-mode__desc">重新生成所有页面和大纲</span>
                </div>
              </label>
              <label class="regen-mode" :class="{ 'regen-mode--active': mode === 'partial' }">
                <input
                  type="radio"
                  name="regen-mode"
                  value="partial"
                  v-model="mode"
                  class="regen-mode__radio"
                />
                <div class="regen-mode__content">
                  <span class="regen-mode__label">选择性重新生成</span>
                  <span class="regen-mode__desc">仅重新生成选中的页面</span>
                </div>
              </label>
            </div>

            <!-- Page tree (partial mode only) -->
            <div v-if="mode === 'partial' && wiki" class="regen-tree">
              <div
                v-for="section in wiki.sections"
                :key="section.id"
                class="regen-section"
              >
                <!-- Section row -->
                <label class="regen-row regen-row--section">
                  <input
                    type="checkbox"
                    :ref="(el) => setSectionIndeterminate(el as HTMLInputElement | null, section.id)"
                    :checked="getSectionState(section.id) === 'all'"
                    @change="toggleSection(section.id)"
                    class="regen-checkbox"
                  />
                  <span class="regen-row__label regen-row__label--section">{{ section.title }}</span>
                </label>

                <!-- Page rows -->
                <label
                  v-for="page in section.pages"
                  :key="page.id"
                  class="regen-row regen-row--page"
                >
                  <input
                    type="checkbox"
                    :checked="selectedPageIds.has(page.id)"
                    @change="togglePage(page.id)"
                    class="regen-checkbox"
                  />
                  <span class="regen-row__label">{{ page.title }}</span>
                  <span
                    v-if="isPageEmpty(page.content_md)"
                    class="regen-badge regen-badge--empty"
                    title="该页面内容为空"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                      <line x1="12" y1="9" x2="12" y2="13"/>
                      <line x1="12" y1="17" x2="12.01" y2="17"/>
                    </svg>
                    内容为空
                  </span>
                </label>
              </div>

              <!-- Empty selection hint -->
              <div v-if="selectedPageIds.size === 0" class="regen-hint">
                请至少选择一个页面
              </div>
            </div>
          </div>

          <!-- Footer -->
          <div class="regen-modal__footer">
            <button class="btn btn-secondary" @click="handleClose">取消</button>
            <button
              class="btn btn-primary"
              @click="handleConfirm"
              :disabled="!canConfirm"
            >
              开始生成
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.regen-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.regen-modal {
  width: 100%;
  max-width: 480px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.18), 0 8px 24px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  max-height: 70vh;
  overflow-y: auto;
  overflow-x: hidden;
}

[data-theme="dark"] .regen-modal {
  background: #161616;
  border-color: #2a2a2a;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55), 0 8px 24px rgba(0, 0, 0, 0.4);
}

/* Header */
.regen-modal__header {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--bg-primary);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border-color);
}

[data-theme="dark"] .regen-modal__header {
  background: #161616;
}

.regen-modal__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.regen-modal__close {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  color: var(--text-muted);
  transition: background 0.15s, color 0.15s;
}

.regen-modal__close svg {
  width: 15px;
  height: 15px;
}

.regen-modal__close:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}

/* Body */
.regen-modal__body {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Mode options */
.regen-modes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.regen-mode {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.regen-mode:hover {
  background: var(--bg-hover);
}

.regen-mode--active {
  border-color: var(--color-primary);
  background: var(--color-primary-light, rgba(99, 102, 241, 0.06));
}

.regen-mode__radio {
  margin-top: 2px;
  flex-shrink: 0;
  accent-color: var(--color-primary);
}

.regen-mode__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.regen-mode__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: 1.4;
}

.regen-mode__desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}

/* Page tree */
.regen-tree {
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: var(--radius);
}

.regen-section {
  display: flex;
  flex-direction: column;
}

.regen-section + .regen-section {
  border-top: 1px solid var(--border-color);
}

.regen-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  cursor: pointer;
  transition: background 0.1s;
}

.regen-row:hover {
  background: var(--bg-hover);
}

.regen-row--section {
  background: var(--bg-secondary);
  padding: 8px 12px;
}

.regen-row--page {
  padding-left: 28px;
}

.regen-row--page + .regen-row--page {
  border-top: 1px solid var(--border-color);
}

.regen-checkbox {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  cursor: pointer;
  accent-color: var(--color-primary);
}

.regen-row__label {
  font-size: 13px;
  color: var(--text-secondary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.regen-row__label--section {
  font-weight: 500;
  color: var(--text-primary);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* Empty content badge */
.regen-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 4px;
  line-height: 1.4;
}

.regen-badge--empty {
  color: #b45309;
  background: rgba(217, 119, 6, 0.1);
  border: 1px solid rgba(217, 119, 6, 0.25);
}

[data-theme="dark"] .regen-badge--empty {
  color: #fbbf24;
  background: rgba(251, 191, 36, 0.1);
  border-color: rgba(251, 191, 36, 0.2);
}

.regen-badge svg {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
}

/* Empty selection hint */
.regen-hint {
  padding: 10px 14px;
  font-size: 12px;
  color: var(--text-muted);
  border-top: 1px solid var(--border-color);
  text-align: center;
}

/* Footer */
.regen-modal__footer {
  position: sticky;
  bottom: 0;
  z-index: 1;
  background: var(--bg-primary);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 18px;
  border-top: 1px solid var(--border-color);
}

[data-theme="dark"] .regen-modal__footer {
  background: #161616;
}

/* Transition */
.regen-fade-enter-active,
.regen-fade-leave-active {
  transition: opacity 0.18s ease;
}

.regen-fade-enter-active .regen-modal,
.regen-fade-leave-active .regen-modal {
  transition: transform 0.18s ease, opacity 0.18s ease;
}

.regen-fade-enter-from,
.regen-fade-leave-to {
  opacity: 0;
}

.regen-fade-enter-from .regen-modal,
.regen-fade-leave-to .regen-modal {
  transform: translateY(-6px);
  opacity: 0;
}
</style>
