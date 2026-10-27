import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import DetailDrawer from './DetailDrawer'
import StatusChip from './StatusChip'
import StatMetricBar from './StatMetricBar'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

function mount(ui: React.ReactElement): Root {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(ui))
  return root
}

describe('DetailDrawer', () => {
  it('open=false 时不渲染；open=true 渲染标题与内容并锁定背景滚动', () => {
    let root = mount(
      <DetailDrawer open={false} onClose={() => {}} title="连接详情">
        <div>内容体</div>
      </DetailDrawer>,
    )
    expect(document.body.textContent).not.toContain('连接详情')
    act(() => root.unmount())

    root = mount(
      <DetailDrawer open onClose={() => {}} title="连接详情" subtitle="e2e-srv">
        <div data-body>内容体</div>
      </DetailDrawer>,
    )
    expect(document.body.textContent).toContain('连接详情')
    expect(document.body.style.overflow).toBe('hidden')
    expect(document.querySelector('[role="dialog"]')).not.toBeNull()
    act(() => root.unmount())
    expect(document.body.style.overflow).toBe('')
  })

  it('Esc 与背景点击触发 onClose；Tab 切换回调带 id', () => {
    const onClose = vi.fn()
    const onTabChange = vi.fn()
    const root = mount(
      <DetailDrawer
        open
        onClose={onClose}
        title="工具详情"
        tabs={[
          { id: 'overview', label: '概览' },
          { id: 'schema', label: 'Schema' },
        ]}
        activeTab="overview"
        onTabChange={onTabChange}
      >
        <div>内容</div>
      </DetailDrawer>,
    )
    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(onClose).toHaveBeenCalledTimes(1)

    const backdrop = document.querySelector('[aria-label="关闭详情"]') as HTMLButtonElement
    act(() => backdrop.click())
    expect(onClose).toHaveBeenCalledTimes(2)

    const schemaTab = [...document.querySelectorAll('[role="tab"]')].find(
      (el) => el.textContent === 'Schema',
    ) as HTMLButtonElement
    act(() => schemaTab.click())
    expect(onTabChange).toHaveBeenCalledWith('schema')
    act(() => root.unmount())
  })

  it('disabled Tab 为置灰壳位：不可点击、带 data-stub 标记与常驻提示', () => {
    const onTabChange = vi.fn()
    const root = mount(
      <DetailDrawer
        open
        onClose={() => {}}
        title="工具详情"
        tabs={[
          { id: 'overview', label: '概览' },
          {
            id: 'ai', label: 'AI 语义', disabled: true,
            disabledHint: 'AI 语义契约建议属下一批',
          },
        ]}
        activeTab="overview"
        onTabChange={onTabChange}
      >
        <div>内容</div>
      </DetailDrawer>,
    )
    const stubTab = document.querySelector('[data-stub="demo"]')
    expect(stubTab?.textContent).toContain('AI 语义')
    expect(stubTab?.getAttribute('aria-disabled')).toBe('true')
    expect(document.body.textContent).toContain('「AI 语义」AI 语义契约建议属下一批')
    act(() => { (stubTab as HTMLElement).click() })
    expect(onTabChange).not.toHaveBeenCalled()
    act(() => root.unmount())
  })
})

describe('StatusChip', () => {
  it('按 tone 渲染文字与色点，文字始终存在', () => {
    const root = mount(
      <div>
        <StatusChip tone="success">已审核</StatusChip>
        <StatusChip tone="warn">待审核</StatusChip>
        <StatusChip tone="danger">已停用</StatusChip>
      </div>,
    )
    const chips = document.querySelectorAll('span')
    expect(chips.length).toBeGreaterThanOrEqual(3)
    expect(document.body.textContent).toContain('已审核')
    expect(document.body.textContent).toContain('待审核')
    act(() => root.unmount())
  })
})

describe('StatMetricBar', () => {
  it('渲染全部指标；空列表不渲染', () => {
    let root = mount(
      <StatMetricBar
        items={[
          { label: '连接总数', value: 12 },
          { label: '连接异常', value: 1, tone: 'danger' },
        ]}
      />,
    )
    expect(document.body.textContent).toContain('连接总数')
    expect(document.body.textContent).toContain('12')
    act(() => root.unmount())

    root = mount(<StatMetricBar items={[]} />)
    expect(document.body.textContent).toBe('')
    act(() => root.unmount())
  })
})
