import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({
  pathname: '/selection-workbench',
  query: '',
  push: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))

vi.mock('./SelectionFunnelPanel', () => ({
  default: () => <div data-panel="funnel">选品漏斗面板</div>,
}))

vi.mock('./SelectionDecisionPanel', () => ({
  default: () => <div data-panel="decision">选品决策面板</div>,
}))

import SelectionWorkbench from './SelectionWorkbench'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  navigation.query = ''
  navigation.push.mockClear()
})

function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<SelectionWorkbench />))
  return { root, container }
}

describe('SelectionWorkbench', () => {
  it('默认展示漏斗 Tab，且只挂载一个面板', () => {
    const { root, container } = mount()

    expect(container.textContent).toContain('选品工作台')
    expect(container.textContent).toContain('选品漏斗面板')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('选品漏斗')

    act(() => root.unmount())
  })

  it('读取决策 Tab 并通过 URL push 切换', () => {
    navigation.query = 'tab=decision&category=耳机'
    const { root, container } = mount()

    expect(container.textContent).toContain('选品决策面板')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    act(() => container.querySelector('[role="tab"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(navigation.push).toHaveBeenCalledWith('/selection-workbench?tab=funnel&category=%E8%80%B3%E6%9C%BA')

    act(() => root.unmount())
  })
})
