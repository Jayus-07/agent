import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getSystemHealth = vi.hoisted(() => vi.fn())
const listTraces = vi.hoisted(() => vi.fn())
const getDegradationAlerts = vi.hoisted(() => vi.fn())
const getSkillHealth = vi.hoisted(() => vi.fn())

vi.mock('@/api/observability', () => ({ getSystemHealth, listTraces, getDegradationAlerts, getSkillHealth }))
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
  getDegradationAlerts.mockResolvedValue({ alerts: [], total: 0 })
  getSkillHealth.mockResolvedValue({ skills: [], trace_window: 300 })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

describe('服务健康与告警页面', () => {
  it('保留降级事件流、Skill 统计与健康快照，并隐藏自由文本', async () => {
    getDegradationAlerts.mockResolvedValueOnce({
      total: 3,
      alerts: [{ timestamp: '2026-10-14T08:00:00Z', level: 'warn', code: 'workflow_deadline_exceeded', message: 'private event body' }],
    })
    getSkillHealth.mockResolvedValueOnce({
      trace_window: 300,
      skills: [{ name: 'rag.search', type: 'tool_call', total: 4, success: 3, error: 1, skipped: 0, neutral: 0, real_errors: 1, retries: 2, avg_duration_ms: 25, success_rate: 0.75, last_error_kind: 'failure', last_error: 'private skill error' }],
    })
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
    expect(container.textContent).toContain('历史共 3 条')
    expect(container.textContent).toContain('workflow_deadline_exceeded')
    expect(container.textContent).toContain('Skill 健康统计')
    expect(container.textContent).toContain('rag.search')
    expect(container.textContent).toContain('75%')
    expect(container.textContent).toContain('故障')
    expect(container.querySelector('a[href="/observability/traces/trace-authorized-123456"]')).toBeTruthy()
    for (const sensitive of ['private customer input', 'private answer', 'private error message', 'private event body', 'private skill error', 'must-not-render']) {
      expect(container.textContent).not.toContain(sensitive)
    }
  })

  it('各健康数据源失败时分别标记不可用', async () => {
    getSystemHealth.mockRejectedValueOnce(new Error('private backend detail'))
    listTraces.mockRejectedValueOnce(new Error('private trace detail'))
    getDegradationAlerts.mockRejectedValueOnce(new Error('private alert detail'))
    getSkillHealth.mockRejectedValueOnce(new Error('private skill detail'))
    const container = mountPage()

    await act(async () => {
      const root = mounted[mounted.length - 1].root
      root.render(<AlertsPage />)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('服务快照不可用')
    expect(container.textContent).toContain('异常样本不可用')
    expect(container.textContent).toContain('降级事件不可用')
    expect(container.textContent).toContain('Skill 健康统计不可用')
    expect(container.textContent).toContain('此状态不代表没有告警')
    expect(container.textContent).not.toContain('当前授权 Trace 样本中没有')
    expect(container.textContent).not.toContain('private backend detail')
    expect(container.textContent).not.toContain('private trace detail')
  })
})
