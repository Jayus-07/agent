import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getSystemHealth = vi.hoisted(() => vi.fn())
const listTraces = vi.hoisted(() => vi.fn())

vi.mock('@/api/observability', () => ({ getSystemHealth, listTraces }))
vi.mock('@/components/auth/RoleGate', () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => <a href={href} {...props}>{children}</a>,
}))

import AlertsPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

function mountPage() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })
  return container
}

beforeEach(() => {
  getSystemHealth.mockResolvedValue({
    collected_at: Math.floor(Date.now() / 1000),
    db: { available: true, latency_ms: 2, active_connections: 3 },
    redis: { db0: { available: true }, broker: { available: true }, result: { available: false }, queues: { celery: 0 } },
    worker: { available: true, online_count: 1, workers: [{ active_tasks: 0 }] },
    tasks: { available: true },
  })
  listTraces.mockResolvedValue([])
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

describe('服务健康与告警页面', () => {
  it('展示真实健康快照、保留真实队列零值并隐藏 Trace 私密正文', async () => {
    listTraces.mockResolvedValueOnce([{
      id: 'trace-authorized-123456',
      timestamp: new Date().toISOString(),
      status: 'success',
      question: 'private customer input',
      answer_preview: 'private answer',
      metadata: { business_outcome: 'failed', private_field: 'must-not-render' },
      error: { error_code: 'DOMAIN_FAILED', message: 'private error message' },
    }])
    const container = mountPage()

    await act(async () => {
      const root = mounted[mounted.length - 1].root
      root.render(<AlertsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('服务健康与告警')
    expect(container.textContent).toContain('PostgreSQL')
    expect(container.textContent).toContain('celery: 0')
    expect(container.textContent).toContain('不可用')
    expect(container.textContent).toContain('业务失败')
    expect(container.textContent).toContain('DOMAIN_FAILED')
    expect(container.querySelector('a[href="/observability/traces/trace-authorized-123456"]')).toBeTruthy()
    for (const sensitive of ['private customer input', 'private answer', 'private error message', 'must-not-render']) {
      expect(container.textContent).not.toContain(sensitive)
    }
  })

  it('健康和 Trace API 都失败时分别显示不可用，不把失败解释为空告警', async () => {
    getSystemHealth.mockRejectedValueOnce(new Error('private backend detail'))
    listTraces.mockRejectedValueOnce(new Error('private trace detail'))
    const container = mountPage()

    await act(async () => {
      const root = mounted[mounted.length - 1].root
      root.render(<AlertsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('服务快照不可用')
    expect(container.textContent).toContain('异常样本不可用')
    expect(container.textContent).toContain('此状态不代表没有告警')
    expect(container.textContent).not.toContain('当前授权 Trace 样本中没有')
    expect(container.textContent).not.toContain('private backend detail')
    expect(container.textContent).not.toContain('private trace detail')
  })
})
