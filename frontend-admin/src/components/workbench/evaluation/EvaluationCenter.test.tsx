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
vi.mock('./proto/FailedTracesTab', () => ({ default: () => <div data-panel="traces">失败 Trace</div> }))
const runsProps = vi.hoisted(() => ({ last: {} as Record<string, unknown> }))
vi.mock('./proto/RunsTab', () => ({ default: (props: { promptPrefill?: { key: string; version: string } | null; domain?: string }) => {
  runsProps.last = { promptPrefill: props.promptPrefill ?? null, domain: props.domain ?? null }
  return <div data-panel="runs" data-prompt={props.promptPrefill?.key || ''} data-pv={props.promptPrefill?.version || ''} data-domain={props.domain || ''}>运行评测</div>
} }))
vi.mock('./proto/CompareTab', () => ({ default: () => <div data-panel="compare">结果对比</div> }))
vi.mock('./DomainOverviewBar', () => ({ default: () => <div data-overview="bar">域质量总览</div> }))
vi.mock('@/lib/auth', () => ({ atLeast: () => true }))

import EvaluationCenter from './EvaluationCenter'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => {
  document.body.innerHTML = ''
  navigation.query = ''
  navigation.push.mockClear()
  runsProps.last = {}
})
function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<EvaluationCenter />))
  return { root, container }
}

describe('EvaluationCenter', () => {
  it('默认进入数据集，三个功能区仅挂载当前面板，域导航默认全部', () => {
    const { root, container } = mount()
    expect(container.textContent).toContain('评测中心')
    expect(container.textContent).toContain('数据集')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(3)
    expect(container.querySelector('[data-overview="bar"]')).not.toBeNull()
    expect(container.textContent).toContain('全部')
    expect(container.textContent).toContain('编排智能体')
    act(() => root.unmount())
  })
  it('结果对比 tab 已隐藏，旧入口别名统一收敛到三个核心区', () => {
    navigation.query = 'tab=feedback'
    const feedback = mount()
    expect(feedback.container.querySelector('[data-panel="datasets"]')).not.toBeNull()
    act(() => feedback.root.unmount())

    navigation.query = 'tab=results'
    const results = mount()
    expect(results.container.querySelector('[role="tabpanel"]')?.getAttribute('aria-label')).toBe('运行评测')
    act(() => results.root.unmount())

    navigation.query = 'tab=gates'
    const gates = mount()
    expect(gates.container.querySelector('[data-panel="runs"]')).not.toBeNull()
    act(() => gates.root.unmount())

    // compare 深链不再可达，收敛到运行评测（对比语义归属运行结果）
    navigation.query = 'tab=compare'
    const compare = mount()
    expect(compare.container.querySelector('[data-panel="runs"]')).not.toBeNull()
    act(() => compare.root.unmount())
  })
  it('失败 Trace 深链进入 Trace 功能区', () => {
    navigation.query = 'tab=traces'
    const traces = mount()
    expect(traces.container.querySelector('[data-panel="traces"]')).not.toBeNull()
    expect(traces.container.querySelector('[role="tabpanel"]')?.getAttribute('aria-label')).toBe('失败 Trace')
    act(() => traces.root.unmount())
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
  it('域导航：URL ?domain= 传入 RunsTab，非法值回落全部', () => {
    navigation.query = 'tab=runs&domain=sql'
    const sql = mount()
    expect(sql.container.querySelector('[data-panel="runs"]')?.getAttribute('data-domain')).toBe('sql')
    act(() => sql.root.unmount())

    navigation.query = 'tab=runs&domain=bogus'
    const bogus = mount()
    // 非法域值回落全部（RunsTab 默认 domain='all'）
    expect(bogus.container.querySelector('[data-panel="runs"]')?.getAttribute('data-domain')).toBe('all')
    act(() => bogus.root.unmount())

    navigation.query = 'tab=runs'
    const none = mount()
    expect(none.container.querySelector('[data-panel="runs"]')?.getAttribute('data-domain')).toBe('all')
    act(() => none.root.unmount())
  })
})
