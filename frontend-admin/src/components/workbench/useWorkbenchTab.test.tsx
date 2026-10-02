import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({
  pathname: '/selection-workbench',
  query: 'tab=decision&has_tool=true',
  push: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))

import { useWorkbenchTab } from './useWorkbenchTab'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  navigation.query = 'tab=decision&has_tool=true'
  navigation.push.mockClear()
})

function Probe() {
  const { tab, selectTab } = useWorkbenchTab(['funnel', 'decision'] as const, 'funnel')

  return (
    <div>
      <span data-tab={tab}>{tab}</span>
      <button type="button" onClick={() => selectTab('funnel')}>切换</button>
    </div>
  )
}

function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<Probe />))
  return { root, container }
}

describe('useWorkbenchTab', () => {
  it('从 URL 读取合法 Tab，并通过 push 保留其它查询参数', () => {
    const { root, container } = mount()

    expect(container.querySelector('[data-tab]')?.textContent).toBe('decision')
    act(() => container.querySelector('button')?.click())
    expect(navigation.push).toHaveBeenCalledWith(
      '/selection-workbench?tab=funnel&has_tool=true',
    )

    act(() => root.unmount())
  })

  it('URL 缺失或非法 Tab 时使用默认 Tab', () => {
    navigation.query = 'has_tool=false'
    const first = mount()
    expect(first.container.querySelector('[data-tab]')?.textContent).toBe('funnel')
    act(() => first.root.unmount())

    navigation.query = 'tab=invalid'
    const second = mount()
    expect(second.container.querySelector('[data-tab]')?.textContent).toBe('funnel')
    act(() => second.root.unmount())
  })
})
