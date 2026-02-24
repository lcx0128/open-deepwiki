import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBadge from '@/components/StatusBadge.vue'

describe('StatusBadge', () => {
  it('渲染 pending 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'pending' } })
    expect(wrapper.text()).toContain('等待中')
    expect(wrapper.find('.badge__dot').exists()).toBe(true)
  })

  it('渲染 completed 状态（无脉冲点）', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'completed' } })
    expect(wrapper.text()).toContain('完成')
    expect(wrapper.find('.badge__dot').exists()).toBe(false)
  })

  it('渲染 ready 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'ready' } })
    expect(wrapper.text()).toContain('就绪')
    expect(wrapper.find('.badge__dot').exists()).toBe(false)
  })

  it('渲染 error 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'error' } })
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.find('.badge__dot').exists()).toBe(false)
  })

  it('渲染 failed 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'failed' } })
    expect(wrapper.text()).toContain('失败')
  })

  it('渲染 cloning 状态（有脉冲点）', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'cloning' } })
    expect(wrapper.text()).toContain('克隆中')
    expect(wrapper.find('.badge__dot').exists()).toBe(true)
  })

  it('渲染 parsing 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'parsing' } })
    expect(wrapper.text()).toContain('解析中')
  })

  it('渲染 embedding 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'embedding' } })
    expect(wrapper.text()).toContain('向量化')
  })

  it('渲染 generating 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'generating' } })
    expect(wrapper.text()).toContain('生成中')
  })

  it('渲染 syncing 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'syncing' } })
    expect(wrapper.text()).toContain('同步中')
    expect(wrapper.find('.badge__dot').exists()).toBe(true)
  })

  it('渲染 cancelled 状态', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'cancelled' } })
    expect(wrapper.text()).toContain('已取消')
  })

  it('sm 尺寸应用 badge--sm 类', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'ready', size: 'sm' } })
    expect(wrapper.find('.badge--sm').exists()).toBe(true)
    expect(wrapper.find('.badge--md').exists()).toBe(false)
  })

  it('md 尺寸（默认）应用 badge--md 类', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'ready' } })
    expect(wrapper.find('.badge--md').exists()).toBe(true)
  })
})
