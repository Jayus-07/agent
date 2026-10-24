import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({ pathname: '/evaluations/center', query: '', push: vi.fn() }))
vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))
vi.mock('./DatasetGovernancePanel', () => ({ default: () => <div data-panel="datasets">数据集中心</div> }))
vi.mock('./DatasetSynthesisPanel', () => ({ default: () => <div data-panel="generate">智能生成</div> }))
vi.mock('./DatasetReviewsPanel', () => ({ default: () => <div data-panel="reviews">候选审核</div> }))
vi.mock('./FeedbackCandidatesPanel', () => ({ default: () => <div data-panel="feedback">反馈候选</div> }))
vi.mock('./EvaluationResultsPanel', () => ({ default: () => <div data-panel="results">评测结果</div> }))
vi.mock('./EvaluationBindingsPanel', () => ({ default: () => <div data-panel="bindings">关联运行</div> }))
vi.mock('./EvaluationOperationsOverview', () => ({ default: () => <div data-panel="overview">真实评测概览</div> }))
vi.mock('./EvaluationSuitesPanel', () => ({ default: () => <div data-panel="suites">评测方案</div> }))
vi.mock('./EvaluationRunComparisonPanel', () => ({ default: () => <div data-panel="compare">实验对比</div> }))
vi.mock('./EvaluationReleaseQualityPanel', () => ({ default: () => <div data-panel="gates">发布质量</div> }))
vi.mock('./EvaluationV2ReviewPanel', () => ({ default: () => <div data-panel="v2-review">V2 候选审核</div> }))
vi.mock('./EvaluationV2RunsPanel', () => ({ default: () => <div data-panel="runs">V2 实验运行</div> }))

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
  it('默认进入评测概览，八个功能区仅挂载当前面板', () => {
    const { root, container } = mount()
    expect(container.textContent).toContain('评测中心')
    expect(container.textContent).toContain('真实评测概览')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(8)
    act(() => root.unmount())
  })
  it('保留旧入口别名并支持无效 Tab 回退', () => {
    navigation.query = 'tab=feedback'
    const feedback = mount()
    expect(feedback.container.querySelector('[data-panel="reviews"]')).not.toBeNull()
    expect(feedback.container.querySelector('[data-panel="feedback"]')).not.toBeNull()
    act(() => feedback.root.unmount())

    navigation.query = 'tab=results'
    const results = mount()
    expect(results.container.querySelector('[role="tabpanel"]')?.getAttribute('aria-label')).toBe('实验运行')
    expect(results.container.querySelector('[data-panel="runs"]')).not.toBeNull()
    act(() => results.root.unmount())

    navigation.query = 'tab=bindings&kb_id=kb_demo'
    const bindings = mount()
    expect(bindings.container.querySelector('[role="tabpanel"]')?.getAttribute('aria-label')).toBe('实验运行')
    expect(bindings.container.querySelector('[data-panel="bindings"]')).not.toBeNull()
    expect(bindings.container.querySelector('details[open] summary')?.textContent).toContain('旧版题集关联知识库运行')
    act(() => bindings.root.unmount())

    navigation.query = 'tab=unknown'
    const invalid = mount()
    expect(invalid.container.querySelector('[data-panel="overview"]')).not.toBeNull()
    act(() => invalid.root.unmount())
  })
  it('八区深链接直接定位目标页面', () => {
    navigation.query = 'tab=compare'
    const comparison = mount()
    expect(comparison.container.querySelector('[data-panel="compare"]')).not.toBeNull()
    act(() => comparison.root.unmount())
    navigation.query = 'tab=gates'
    const gates = mount()
    expect(gates.container.querySelector('[data-panel="gates"]')).not.toBeNull()
    act(() => gates.root.unmount())
  })
})
