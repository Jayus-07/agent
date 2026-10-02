import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import WorkbenchShell from './WorkbenchShell'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
})

function mount(onTabChange = vi.fn()): { root: Root; container: HTMLDivElement; onTabChange: ReturnType<typeof vi.fn> } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() =>
    root.render(
      <WorkbenchShell
        title="选品工作台"
        description="统一处理选品任务"
        tabs={[
          { id: 'funnel', label: '选品漏斗' },
          { id: 'decision', label: '选品决策' },
        ]}
        activeTab="funnel"
        onTabChange={onTabChange}
      >
        <div data-panel>当前面板</div>
      </WorkbenchShell>,
    ),
  )
  return { root, container, onTabChange }
}

describe('WorkbenchShell', () => {
  it('渲染标题、Tab 语义和当前面板', () => {
    const { root, container } = mount()

    expect(container.textContent).toContain('选品工作台')
    expect(container.textContent).toContain('统一处理选品任务')
    expect(container.querySelector('[role="tablist"]')).not.toBeNull()
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(2)
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('选品漏斗')
    expect(container.querySelector('[data-panel]')?.textContent).toBe('当前面板')

    act(() => root.unmount())
  })

  it('点击 Tab 调用切换回调并提供可见焦点样式', () => {
    const { root, container, onTabChange } = mount()
    const decisionTab = container.querySelector<HTMLButtonElement>('[role="tab"]:nth-child(2)')

    expect(decisionTab?.className).toContain('focus-visible')
    act(() => decisionTab?.click())
    expect(onTabChange).toHaveBeenCalledWith('decision')

    act(() => root.unmount())
  })
})
