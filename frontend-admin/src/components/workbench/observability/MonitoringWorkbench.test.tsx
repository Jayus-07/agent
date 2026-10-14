import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({
  pathname: '/observability/monitoring',
  query: '',
  push: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))

vi.mock('./TracesPanel', () => ({ default: () => <div data-panel="traces">问答追踪面板</div> }))
vi.mock('./RuntimeOverview', () => ({ default: () => <div data-panel="overview">运行总览面板</div> }))
vi.mock('./GatewayPanel', () => ({ default: () => <div data-panel="gateway">网关安全面板</div> }))
vi.mock('./TokensPanel', () => ({ default: () => <div data-panel="tokens">Token 用量面板</div> }))

import MonitoringWorkbench from './MonitoringWorkbench'

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
  act(() => root.render(<MonitoringWorkbench />))
  return { root, container }
}

describe('MonitoringWorkbench', () => {
  it('默认展示运行总览并只挂载一个面板', () => {
    const { root, container } = mount()

    expect(container.textContent).toContain('运行控制中心')
    expect(container.textContent).toContain('运行总览面板')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(4)

    act(() => root.unmount())
  })

  it('保留旧 traces Tab 参数，非法值回退到总览', () => {
    navigation.query = 'tab=tokens'
    const tokens = mount()
    expect(tokens.container.textContent).toContain('Token 用量面板')
    act(() => tokens.root.unmount())

    navigation.query = 'tab=traces'
    const traces = mount()
    expect(traces.container.textContent).toContain('问答追踪面板')
    act(() => traces.root.unmount())

    navigation.query = 'tab=unknown'
    const invalid = mount()
    expect(invalid.container.textContent).toContain('运行总览面板')
    act(() => invalid.root.unmount())
  })
})
