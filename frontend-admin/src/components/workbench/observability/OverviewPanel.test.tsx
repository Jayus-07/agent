import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const mocks = vi.hoisted(() => ({
  listTracesPage: vi.fn(),
  getTraceStats: vi.fn(),
  listActiveTraces: vi.fn(),
  navigation: {
    pathname: '/observability/monitoring',
    params: new URLSearchParams('tab=overview'),
    router: { push: vi.fn(), replace: vi.fn() },
  },
}))

vi.mock('@/api/observability', () => ({
  listTracesPage: mocks.listTracesPage,
  getTraceStats: mocks.getTraceStats,
  listActiveTraces: mocks.listActiveTraces,
}))
vi.mock('next/link', () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }))
vi.mock('next/navigation', () => ({
  usePathname: () => mocks.navigation.pathname,
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))

import OverviewPanel from './OverviewPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.listTracesPage.mockReset().mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
  mocks.getTraceStats.mockReset().mockResolvedValue(makeStats())
  mocks.listActiveTraces.mockReset().mockResolvedValue([])
  mocks.navigation.params = new URLSearchParams('tab=overview')
})

function makeStats(overrides: Record<string, unknown> = {}) {
  return {
    total: 3,
    total_24h: 3,
    counts: {
      success: 1, failed: 0, error: 2, degraded: 0, partial: 0,
      timeout: 0, cancelled: 0, rejected: 0, unknown: 0,
    },
    running_count: 1,
    success_rate: 1 / 3,
    avg_duration_ms: 800,
    p50_duration_ms: 500,
    p95_duration_ms: 4000,
    error_count: 2,
    total_cost_usd: 0,
    sources: {},
    window_hours: 24,
    records_considered: 3,
    retention_limit: 5000,
    observed_at: new Date().toISOString(),
    ...overrides,
  }
}

function makeTrace(id: string, overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    id,
    request_id: `request-${id}`,
    timestamp: new Date().toISOString(),
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 1000,
    model: { name: 'doubao-seed-2.0-mini', provider: 'volcengine' },
    usage: { total_tokens: 500 },
    cost: {},
    cost_cny: 0.0003,
    error: {},
    metadata: {},
    spans: [],
    status: 'success',
    sla: { threshold_ms: 10000, breached: false },
    ...overrides,
  } as TraceRecord
}

async function mountOverview(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<OverviewPanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('OverviewPanel（运行总览重构版）', () => {
  it('卡片消费 stats 与授权活跃端点，终态分布全部可点击筛选', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    mocks.listActiveTraces.mockResolvedValue([makeTrace('live-1', { status: 'running' })])
    const container = await mountOverview()

    expect(mocks.getTraceStats).toHaveBeenCalledWith(24)
    expect(mocks.listActiveTraces).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('33.3%')
    expect(container.textContent).toContain('500 毫秒')
    // 运行中来自活跃端点实时计数
    expect(container.textContent).toContain('运行中（实时）')
    expect(container.textContent).toContain('授权活跃端点实时计数')
    // 终态分布：error 行可点击进入筛选
    const errorLinks = container.querySelectorAll('a[href*="status=error"]')
    expect(errorLinks.length).toBeGreaterThan(0)
  })

  it('业务域分布按样本聚合并标注口径', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [
        makeTrace('a1', { workflow_name: 'agent', source: 'ai_assistant' }),
        makeTrace('a2', { workflow_name: 'rag_agent', source: '' }),
        makeTrace('a3', { workflow_name: 'agent', source: 'ai_assistant', status: 'error', error: { code: 'X' } }),
      ],
      total: 3, offset: 0, limit: 200, has_more: false, retention_limit: 5000,
    })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    const container = await mountOverview()

    expect(container.textContent).toContain('业务域分布')
    expect(container.textContent).toContain('AI 助手')
    // rag_agent 的域口径是「平台工作流」（RAG 链是类型徽标文案，不是域）
    expect(container.textContent).toContain('平台工作流')
    expect(container.textContent).toContain('样本聚合')
  })

  it('最近错误区展示错误码与消息并提供筛选入口', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [makeTrace('e1', { status: 'error', error: { code: 'TOOL_EXEC_FAILED', message: 'calculate_tool 执行失败' } })],
      total: 1, offset: 0, limit: 200, has_more: false, retention_limit: 5000,
    })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    const container = await mountOverview()

    expect(container.textContent).toContain('最近错误')
    expect(container.textContent).toContain('TOOL_EXEC_FAILED')
    expect(container.textContent).toContain('calculate_tool 执行失败')
  })

  it('首次失败显示错误态而非伪造数据', async () => {
    mocks.listTracesPage.mockRejectedValue({ status: 503 })
    const container = await mountOverview()
    expect(container.textContent).toContain('运行数据暂不可用')
    expect(container.textContent).toContain('没有使用缓存统计或模拟数据')
  })
})
