import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const navigation = vi.hoisted(() => ({
  pathname: '/knowledge/workbench',
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

vi.mock('./DocumentsPanel', () => ({ default: () => <div data-panel="documents">文档面板</div> }))
vi.mock('./PendingReviewPanel', () => ({ default: () => <div data-panel="pending">待复核面板</div> }))
vi.mock('./UploadFailuresPanel', () => ({ default: () => <div data-panel="failures">入库失败面板</div> }))
vi.mock('./KeywordsPanel', () => ({ default: () => <div data-panel="keywords">词库面板</div> }))
vi.mock('./OperationsPanel', () => ({ default: () => <div data-panel="operations">操作日志面板</div> }))

import KnowledgeWorkbench from './KnowledgeWorkbench'

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
  act(() => root.render(<KnowledgeWorkbench />))
  return { root, container }
}

describe('KnowledgeWorkbench', () => {
  it('默认展示文档面板，五个 Tab 中只挂载一个', () => {
    const { root, container } = mount()

    expect(container.textContent).toContain('知识库工作台')
    expect(container.textContent).toContain('文档面板')
    expect(container.querySelectorAll('[data-panel]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(5)

    act(() => root.unmount())
  })

  it('非法 Tab 回退到文档，合法 operations Tab 只挂载操作日志', () => {
    navigation.query = 'tab=invalid'
    const invalid = mount()
    expect(invalid.container.textContent).toContain('文档面板')
    act(() => invalid.root.unmount())

    navigation.query = 'tab=operations&operation=upload'
    const operations = mount()
    expect(operations.container.textContent).toContain('操作日志面板')
    expect(operations.container.querySelectorAll('[data-panel]')).toHaveLength(1)
    act(() => operations.root.unmount())
  })
})
