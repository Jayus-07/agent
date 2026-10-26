import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({ pathname: '/evaluations/center', query: '', push: vi.fn() }))
vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))
vi.mock('./proto/DatasetsTab', () => ({ default: () => <div data-panel="datasets">数据集</div> }))
vi.mock('./proto/RunsTab', () => ({ default: ({ promptPrefill }: { promptPrefill?: { key: string; version: string } | null }) => (
  <div data-panel="runs" data-prompt={promptPrefill?.key || ''} data-pv={promptPrefill?.version || ''}>运行评测</div>
) }))
vi.mock('./proto/CompareTab', () => ({ default: () => <div data-panel="compare">结果对比</div> }))

import EvaluationCenter from './EvaluationCenter'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => {
  document.body.innerHTML = ''
  navigation.query = ''
  navigation.push.mockClear()
})
function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<EvaluationCenter />))
  return { root, container }
}

describe('EvaluationCenter', () => {
  it('默认进入数据集，三个功能区仅挂载当前面板', () => {
    const { root, container } = mount()
    expect(container.textContent).toContain('评测中心')
    expect(container.textContent).toContain('数据集')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(3)
    act(() => root.unmount())
  })
  it('旧入口别名统一收敛到三个核心区', () => {
    navigation.query = 'tab=feedback'
    const feedback = mount()
    expect(feedback.container.querySelector('[data-panel="datasets"]')).not.toBeNull()
    act(() => feedback.root.unmount())

    navigation.query = 'tab=results'
    const results = mount()
    expect(results.container.querySelector('[role="tabpanel"]')?.getAttribute('aria-label')).toBe('运行评测')
    act(() => results.root.unmount())

    navigation.query = 'tab=bindings'
    const bindings = mount()
    expect(bindings.container.querySelector('[data-panel="runs"]')).not.toBeNull()
    act(() => bindings.root.unmount())

    navigation.query = 'tab=gates'
    const gates = mount()
    expect(gates.container.querySelector('[data-panel="compare"]')).not.toBeNull()
    act(() => gates.root.unmount())

    navigation.query = 'tab=unknown'
    const invalid = mount()
    expect(invalid.container.querySelector('[data-panel="datasets"]')).not.toBeNull()
    act(() => invalid.root.unmount())
  })
  it('Prompt 治理深链预填运行参数', () => {
    navigation.query = 'tab=runs&prompt=rag.qa&pv=23'
    const deep = mount()
    const runs = deep.container.querySelector('[data-panel="runs"]')
    expect(runs?.getAttribute('data-prompt')).toBe('rag.qa')
    expect(runs?.getAttribute('data-pv')).toBe('23')
    act(() => deep.root.unmount())

    navigation.query = 'tab=runs&prompt=rag.qa'
    const noVersion = mount()
    expect(noVersion.container.querySelector('[data-panel="runs"]')?.getAttribute('data-pv')).toBe('')
    act(() => noVersion.root.unmount())
  })
})
