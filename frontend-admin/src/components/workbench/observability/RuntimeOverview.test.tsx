import { act } from 'react'
import type { ReactNode } from 'react'
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
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => <a href={href} {...props}>{children}</a>,
}))
vi.mock('next/navigation', () => ({
  usePathname: () => mocks.navigation.pathname,
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))

import RuntimeOverview from './RuntimeOverview'

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

function makeTrace(status: TraceRecord['status'] = 'running', overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    id: 'active-trace',
    request_id: 'request-1',
    timestamp: new Date().toISOString(),
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 0,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    status,
    ...overrides,
  }
}

function makeStats(overrides: Record<string, unknown> = {}) {
  return {
    total: 0,
    total_24h: 0,
    counts: {
      success: 0, failed: 0, error: 0, degraded: 0, partial: 0,
      timeout: 0, cancelled: 0, rejected: 0, unknown: 0,
    },
    running_count: 0,
    success_rate: 0,
    avg_duration_ms: 0,
    p50_duration_ms: 0,
    p95_duration_ms: 0,
    error_count: 0,
    total_cost_usd: 0,
    sources: {},
    window_hours: 24,
    records_considered: 0,
    retention_limit: 5000,
    observed_at: new Date().toISOString(),
    ...overrides,
  }
}

async function mountOverview(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<RuntimeOverview />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('RuntimeOverview', () => {
  it('运行中卡片读取授权活跃端点计数并提供筛选入口', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [makeTrace()], total: 1, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.listActiveTraces.mockResolvedValue([makeTrace(), makeTrace('running', { id: 'active-2' })])
    const container = await mountOverview()

    expect(mocks.listTracesPage).toHaveBeenCalledWith(expect.objectContaining({ limit: 200, offset: 0, excludeRunning: true }))
    expect(mocks.getTraceStats).toHaveBeenCalledWith(24)
    expect(mocks.listActiveTraces).toHaveBeenCalledTimes(1)
    const runningCard = container.querySelector('a[href*="status=running"]')
    expect(runningCard).toBeTruthy()
    expect(runningCard?.textContent).toContain('2')
    // 运行中计数来自授权活跃端点，不得从历史留存样本推断
    expect(runningCard?.textContent).not.toContain('BLOCKED')
    expect(container.textContent).toContain('没有可确认的终态记录')
    expect(container.querySelector('tbody tr')).toBeNull()
  })

  it('活跃端点失败时运行中卡片显示未采集而非伪造计数', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [makeTrace('success', { id: 'ok-1' })], total: 1, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.listActiveTraces.mockRejectedValue(new Error('unavailable'))
    const container = await mountOverview()

    const runningCard = container.querySelector('a[href*="status=running"]')
    expect(runningCard?.textContent).toContain('未采集')
  })

  it('为部分成功单独统计并提供对应筛选入口', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [makeTrace('partial', { id: 'partial-trace', metadata: { business_outcome: 'partial' } })],
      total: 1, offset: 0, limit: 200, has_more: false, retention_limit: 5000,
    })
    mocks.getTraceStats.mockResolvedValue(makeStats({
      total: 1,
      counts: { ...makeStats().counts, partial: 1 },
    }))
    const container = await mountOverview()

    const partialCard = container.querySelector<HTMLAnchorElement>('a[href*="status=partial"]')
    expect(partialCard?.textContent).toContain('部分成功')
    expect(partialCard?.textContent).toContain('1')
  })

  it('总览记录数与同一时间范围的终态执行列表一致', async () => {
    mocks.navigation.params = new URLSearchParams('tab=overview&range=24h')
    const statuses: TraceRecord['status'][] = [
      'success', 'failed', 'error', 'degraded', 'partial', 'timeout', 'cancelled', 'rejected', 'running',
    ]
    mocks.listTracesPage.mockResolvedValue({
      traces: [
        ...statuses.map((status) => makeTrace(status, { id: `trace-${status}` })),
        makeTrace('success', { id: 'trace-unknown', status: undefined }),
        makeTrace('success', { id: 'future', timestamp: new Date(Date.now() + 60_000).toISOString() }),
        makeTrace('success', { id: 'outside-window', timestamp: new Date(Date.now() - 25 * 60 * 60_000).toISOString() }),
      ],
      total: 9, offset: 0, limit: 200, has_more: false, retention_limit: 5000,
    })
    mocks.getTraceStats.mockResolvedValue(makeStats({
      total: 9,
      counts: {
        ...makeStats().counts,
        success: 1, failed: 1, error: 1, degraded: 1, partial: 1,
        timeout: 1, cancelled: 1, rejected: 1, unknown: 1,
      },
    }))

    const container = await mountOverview()
    const recordCard = container.querySelector<HTMLAnchorElement>('a[href*="status=all"]')

    expect(recordCard?.querySelector('strong')?.textContent).toBe('9')
    expect(container.querySelector('[data-trace-id="trace-running"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="future"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="outside-window"]')).toBeNull()
  })
})
