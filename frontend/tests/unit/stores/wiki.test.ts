import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWikiStore } from '@/stores/wiki'
import type { WikiResponse } from '@/api/wiki'

const mockWiki: WikiResponse = {
  id: 'w-001',
  repo_id: 'r-001',
  title: 'Test Wiki',
  llm_provider: 'openai',
  llm_model: 'gpt-4o',
  created_at: '2026-02-21T00:00:00Z',
  sections: [
    {
      id: 's-1',
      title: '第一章',
      order_index: 0,
      pages: [
        {
          id: 'p-1',
          title: '架构总览',
          importance: 'high',
          content_md: '# 架构总览\n\n这是内容。',
          relevant_files: ['src/main.py'],
          order_index: 0,
        },
        {
          id: 'p-2',
          title: '模块说明',
          importance: 'medium',
          content_md: '# 模块说明\n\n这是模块内容。',
          relevant_files: ['src/modules.py'],
          order_index: 1,
        },
      ],
    },
    {
      id: 's-2',
      title: '第二章',
      order_index: 1,
      pages: [
        {
          id: 'p-3',
          title: 'API 参考',
          importance: 'low',
          content_md: '# API 参考',
          relevant_files: [],
          order_index: 0,
        },
      ],
    },
  ],
}

describe('useWikiStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('初始状态为空', () => {
    const store = useWikiStore()
    expect(store.wiki).toBeNull()
    expect(store.activeSectionId).toBeNull()
    expect(store.activePageId).toBeNull()
    expect(store.isLoading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.activePage).toBeNull()
    expect(store.activeSection).toBeNull()
    expect(store.totalPages).toBe(0)
  })

  it('setWiki 设置 wiki 数据', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    expect(store.wiki).toEqual(mockWiki)
    expect(store.error).toBeNull()
  })

  it('setWiki 自动选中第一个 section 和 page', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    expect(store.activeSectionId).toBe('s-1')
    expect(store.activePageId).toBe('p-1')
  })

  it('activePage 返回当前激活页面', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    expect(store.activePage?.id).toBe('p-1')
    expect(store.activePage?.title).toBe('架构总览')
  })

  it('activeSection 返回当前激活章节', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    expect(store.activeSection?.id).toBe('s-1')
    expect(store.activeSection?.title).toBe('第一章')
  })

  it('setActivePage 切换到指定页面', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    store.setActivePage('s-2', 'p-3')
    expect(store.activeSectionId).toBe('s-2')
    expect(store.activePageId).toBe('p-3')
    expect(store.activePage?.title).toBe('API 参考')
  })

  it('activePage 可以跨 section 查找', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    store.setActivePage('s-2', 'p-3')
    expect(store.activePage?.id).toBe('p-3')
  })

  it('totalPages 计算总页数', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    expect(store.totalPages).toBe(3) // 2 + 1
  })

  it('clearWiki 清空所有状态', () => {
    const store = useWikiStore()
    store.setWiki(mockWiki)
    store.clearWiki()
    expect(store.wiki).toBeNull()
    expect(store.activeSectionId).toBeNull()
    expect(store.activePageId).toBeNull()
    expect(store.error).toBeNull()
    expect(store.totalPages).toBe(0)
  })

  it('activePage 在无 wiki 时返回 null', () => {
    const store = useWikiStore()
    expect(store.activePage).toBeNull()
  })

  it('activeSection 在无 wiki 时返回 null', () => {
    const store = useWikiStore()
    expect(store.activeSection).toBeNull()
  })
})
