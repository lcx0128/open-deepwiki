import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ProgressBar from '@/components/ProgressBar.vue'

describe('ProgressBar', () => {
  const defaultProps = {
    status: 'cloning',
    progressPct: 10,
    currentStage: '正在克隆仓库...',
  }

  it('渲染 5 个阶段步骤', () => {
    const wrapper = mount(ProgressBar, { props: defaultProps })
    const stages = wrapper.findAll('.stage')
    expect(stages).toHaveLength(5)
  })

  it('显示进度百分比', () => {
    const wrapper = mount(ProgressBar, { props: { ...defaultProps, progressPct: 35 } })
    expect(wrapper.text()).toContain('35%')
  })

  it('当进度超过 100 时钳位到 100', () => {
    const wrapper = mount(ProgressBar, { props: { ...defaultProps, progressPct: 150 } })
    expect(wrapper.text()).toContain('100%')
  })

  it('当进度小于 0 时钳位到 0', () => {
    const wrapper = mount(ProgressBar, { props: { ...defaultProps, progressPct: -10 } })
    expect(wrapper.text()).toContain('0%')
  })

  it('显示当前阶段文本', () => {
    const wrapper = mount(ProgressBar, { props: defaultProps })
    expect(wrapper.text()).toContain('正在克隆仓库...')
  })

  it('failed 状态显示错误信息', () => {
    const wrapper = mount(ProgressBar, {
      props: {
        ...defaultProps,
        status: 'failed',
        errorMsg: '连接超时',
      },
    })
    expect(wrapper.text()).toContain('处理失败')
    expect(wrapper.text()).toContain('连接超时')
  })

  it('failed 状态无 errorMsg 时不显示错误区', () => {
    const wrapper = mount(ProgressBar, {
      props: { ...defaultProps, status: 'failed' },
    })
    expect(wrapper.find('.error-msg').exists()).toBe(false)
  })

  it('completed 状态显示完成勾选', () => {
    const wrapper = mount(ProgressBar, {
      props: { ...defaultProps, status: 'completed', progressPct: 100 },
    })
    expect(wrapper.text()).toContain('✓')
  })

  it('有文件信息时显示文件计数', () => {
    const wrapper = mount(ProgressBar, {
      props: {
        ...defaultProps,
        filesProcessed: 12,
        filesTotal: 34,
      },
    })
    expect(wrapper.text()).toContain('12/34 文件')
  })

  it('filesTotal 为 0 时不显示文件计数', () => {
    const wrapper = mount(ProgressBar, {
      props: { ...defaultProps, filesTotal: 0, filesProcessed: 0 },
    })
    expect(wrapper.text()).not.toContain('/0 文件')
  })

  it('parsing 阶段正确高亮', () => {
    const wrapper = mount(ProgressBar, {
      props: { ...defaultProps, status: 'parsing', progressPct: 30 },
    })
    const stages = wrapper.findAll('.stage')
    expect(stages[1].classes()).toContain('stage--active') // index 1 = parsing
    expect(stages[0].classes()).toContain('stage--done')   // index 0 = cloning (done)
  })
})
