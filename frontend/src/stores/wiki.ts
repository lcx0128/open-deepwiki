import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { WikiResponse, WikiPage, WikiSection } from '@/api/wiki'

export const useWikiStore = defineStore('wiki', () => {
  const wiki = ref<WikiResponse | null>(null)
  const activeSectionId = ref<string | null>(null)
  const activePageId = ref<string | null>(null)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const activePage = computed<WikiPage | null>(() => {
    if (!wiki.value || !activePageId.value) return null
    for (const section of wiki.value.sections) {
      const page = section.pages.find(p => p.id === activePageId.value)
      if (page) return page
    }
    return null
  })

  const activeSection = computed<WikiSection | null>(() => {
    if (!wiki.value || !activeSectionId.value) return null
    return wiki.value.sections.find(s => s.id === activeSectionId.value) || null
  })

  const totalPages = computed(() => {
    if (!wiki.value) return 0
    return wiki.value.sections.reduce((sum, s) => sum + s.pages.length, 0)
  })

  function setWiki(data: WikiResponse) {
    wiki.value = data
    error.value = null
    // 默认选中第一个 section 的第一个 page
    if (data.sections.length > 0 && data.sections[0].pages.length > 0) {
      activeSectionId.value = data.sections[0].id
      activePageId.value = data.sections[0].pages[0].id
    }
  }

  function setActivePage(sectionId: string, pageId: string) {
    activeSectionId.value = sectionId
    activePageId.value = pageId
  }

  function clearWiki() {
    wiki.value = null
    activeSectionId.value = null
    activePageId.value = null
    error.value = null
  }

  return {
    wiki,
    activeSectionId,
    activePageId,
    isLoading,
    error,
    activePage,
    activeSection,
    totalPages,
    setWiki,
    setActivePage,
    clearWiki,
  }
})
