import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAgents = vi.hoisted(() => vi.fn())
const getWorkflowCatalog = vi.hoisted(() => vi.fn())

vi.mock('@/api/registry', () => ({ getAgents, getWorkflowCatalog }))
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
  sessionStorage.clear()
  sessionStorage.setItem('agent.user_info', JSON.stringify({ roles: ['editor'] }))
  getAgents.mockResolvedValue({
    count: 0,
    summary: {},
    agents: [],
  })
  getWorkflowCatalog.mockResolvedValue({ total: 0, workflows: [] })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  sessionStorage.clear()
  vi.clearAllMocks()
})

describe('Agent 与工作流页面', () => {
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

  it('展示实际注册元数据，并通过现有 Trace 列表按 Workflow 下钻', async () => {
    getAgents.mockResolvedValueOnce({
      count: 1,
      summary: { orchestration: 1 },
      agents: [{ name: 'router', label: 'Router', kind: 'orchestration' }],
    })
    getWorkflowCatalog.mockResolvedValueOnce({
      total: 1,
      workflows: [{
        name: 'daily_report',
        description: '生成日报',
        objects: ['report'],
        actions: ['create'],
        examples: [],
        default_kbs: ['analytics'],
      }],
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AgentsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('Agent 与工作流')
    expect(container.textContent).toContain('daily_report')
    expect(container.textContent).toContain('生成日报')
    expect(container.textContent).toContain('业务对象')
    expect(container.textContent).toContain('声明动作')
    expect(container.textContent).not.toContain('analytics')
    expect(container.querySelector('a[href="/observability/monitoring?tab=traces&workflow=daily_report"]')).toBeTruthy()
    expect(container.textContent).toContain('契约版本、实际步骤及运行历史没有由此目录接口返回')
  })

  it('Workflow 接口失败时保留独立成功的 Agent 注册表', async () => {
    getAgents.mockResolvedValueOnce({
      count: 1,
      summary: { orchestration: 1 },
      agents: [{ name: 'router', label: 'Router', kind: 'orchestration' }],
    })
    getWorkflowCatalog.mockRejectedValueOnce(new Error('raw backend detail'))
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AgentsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('router')
    expect(container.textContent).toContain('Workflow 注册表暂不可用')
    expect(container.textContent).not.toContain('raw backend detail')
  })

  it('刷新失败时继续展示上次成功的 Agent 数据并标记刷新失败', async () => {
    getAgents.mockResolvedValueOnce({
      count: 1,
      summary: { orchestration: 1 },
      agents: [{ name: 'router', label: 'Router', kind: 'orchestration' }],
    }).mockRejectedValueOnce(new Error('raw refresh error'))
    getWorkflowCatalog.mockResolvedValueOnce({ total: 0, workflows: [] })
      .mockResolvedValueOnce({ total: 0, workflows: [] })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AgentsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const refreshButton = [...container.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('刷新'))
    expect(refreshButton).toBeTruthy()

    await act(async () => {
      refreshButton?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('router')
    expect(container.textContent).toContain('刷新失败，以下保留上次成功读取的数据。')
    expect(container.textContent).not.toContain('raw refresh error')
  })

  it('Viewer 看不到 Workflow 注册内容，也不会请求其目录接口', async () => {
    sessionStorage.setItem('agent.user_info', JSON.stringify({ roles: ['viewer'] }))
    getAgents.mockResolvedValueOnce({
      count: 1,
      summary: { orchestration: 1 },
      agents: [{ name: 'router', label: 'Router', kind: 'orchestration' }],
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<AgentsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('router')
    expect(container.textContent).not.toContain('Workflow 注册')
    expect(container.textContent).not.toContain('Skill 与 Capability')
    expect(container.textContent).not.toContain('Tool 注册表')
    expect(getWorkflowCatalog).not.toHaveBeenCalled()
  })
})
