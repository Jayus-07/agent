import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAgents = vi.hoisted(() => vi.fn())

vi.mock('@/api/registry', () => ({ getAgents }))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

import AgentsPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

beforeEach(() => {
  getAgents.mockResolvedValue({
    count: 0,
    summary: {},
    agents: [],
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

describe('Agent 节点页面', () => {
  it('说明 Agent、Skill、Tool 的职责关系并提供下钻入口', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AgentsPage />)
      await Promise.resolve()
    })

    expect(container.textContent).toContain('AI 资产关系总览')
    expect(container.textContent).toContain('Agent：调度与执行节点')
    expect(container.textContent).toContain('Skill：业务能力封装')
    expect(container.textContent).toContain('Tool：原子操作')
    expect(container.textContent).toContain('一个 Agent 可以组织多个 Skill')
    expect(container.querySelector('a[href="/skills"]')).toBeTruthy()
    expect(container.querySelector('a[href="/tools"]')).toBeTruthy()
  })
})
