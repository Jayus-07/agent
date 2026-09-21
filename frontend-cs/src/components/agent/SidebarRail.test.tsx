/**
 * SidebarRail — 收起态任务栏 rail
 *
 * 验收要求：侧边栏收起后不能破坏主要操作。
 * 即：rail 上必须有「展开」「新建任务」两个入口，且点击回调正确触发。
 * 项目未引入 @testing-library，按现有约定用 react-dom 直接渲染。
 */
import { describe, expect, it, vi, afterEach, beforeAll } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import SidebarRail from './SidebarRail'

// react-dom 的 act 需要 React 18 显式声明测试环境，否则告警
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(onExpand: () => void, onNewTask: () => void) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<SidebarRail onExpand={onExpand} onNewTask={onNewTask} />))
  mounted.push({ container, root })
  return container
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('SidebarRail', () => {
  it('提供展开与新建任务入口，点击触发回调', () => {
    const onExpand = vi.fn()
    const onNewTask = vi.fn()
    const container = mount(onExpand, onNewTask)

    // 品牌图标与底部按钮都承担「展开」，加上新建共 3 个按钮
    const buttons = Array.from(container.querySelectorAll('button'))
    const expandButtons = buttons.filter((b) => b.getAttribute('aria-label') === '展开任务栏')
    const newTaskButton = buttons.find((b) => b.getAttribute('aria-label') === '新建任务')

    expect(expandButtons.length).toBe(2)
    expect(newTaskButton).toBeTruthy()

    act(() => { expandButtons[0].click() })
    act(() => { newTaskButton!.click() })
    expect(onExpand).toHaveBeenCalledTimes(1)
    expect(onNewTask).toHaveBeenCalledTimes(1)
  })
})
