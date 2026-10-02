import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({
  pathname: '/evaluations/center',
  query: '',
  push: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}))

vi.mock('@/components/auth/RoleGate', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-role-gate>{children}</div>,
}))
vi.mock('./EvaluationResultsPanel', () => ({ default: () => <div data-panel="results">评测结果面板</div> }))
vi.mock('./DatasetGovernancePanel', () => ({ default: () => <div data-panel="datasets">评测集治理面板</div> }))
vi.mock('./FeedbackCandidatesPanel', () => ({ default: () => <div data-panel="feedback">反馈候选面板</div> }))

import EvaluationCenter from './EvaluationCenter'

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
  act(() => root.render(<EvaluationCenter />))
  return { root, container }
}

describe('EvaluationCenter', () => {
  it('默认展示评测结果，且只挂载一个面板', () => {
    const { root, container } = mount()

    expect(container.textContent).toContain('评测中心')
    expect(container.textContent).toContain('评测结果面板')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(3)

    act(() => root.unmount())
  })

  it('合法 feedback Tab 可直接定位，非法值回退到结果', () => {
    navigation.query = 'tab=feedback'
    const feedback = mount()
    expect(feedback.container.textContent).toContain('反馈候选面板')
    act(() => feedback.root.unmount())

    navigation.query = 'tab=unknown'
    const invalid = mount()
    expect(invalid.container.textContent).toContain('评测结果面板')
    act(() => invalid.root.unmount())
  })
})
