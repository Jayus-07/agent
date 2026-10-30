import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const mocks = vi.hoisted(() => ({
  listTracesPage: vi.fn(),
  getTraceStats: vi.fn(),
  listActiveTraces: vi.fn(),
  listModelHealth: vi.fn(),
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
vi.mock('@/api/modelConfig', () => ({
  listModelHealth: mocks.listModelHealth,
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
  // 第一个用例跑在 afterEach 默认值设置之前，此处先给基线
  mocks.listTracesPage.mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
  mocks.getTraceStats.mockResolvedValue(makeStats())
  mocks.listActiveTraces.mockResolvedValue([])
  mocks.listModelHealth.mockResolvedValue({ items: [] })
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.listTracesPage.mockReset().mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
  mocks.getTraceStats.mockReset().mockResolvedValue(makeStats())
  mocks.listActiveTraces.mockReset().mockResolvedValue([])
  mocks.listModelHealth.mockReset().mockResolvedValue({ items: [] })
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
    sources: { travel: 0, cs: 0, ai_assistant: 2 },
    by_domain: {
      ai_assistant: { total: 2, errors: 1 },
      '': { total: 1, errors: 0 },
    },
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
    // 运行中来自活跃端点实时计数，且可点击进入 running 筛选（P3 保真度）
    expect(container.textContent).toContain('运行中（实时）')
    const runningLinks = container.querySelectorAll('a[href*="status=running"]')
    expect(runningLinks.length).toBeGreaterThan(0)
    // 终态分布：error 行可点击进入筛选
    const errorLinks = container.querySelectorAll('a[href*="status=error"]')
    expect(errorLinks.length).toBeGreaterThan(0)
  })

  it('业务域分布消费服务端 by_domain（classify_trace_source 口径）', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    const container = await mountOverview()

    expect(container.textContent).toContain('业务域分布')
    expect(container.textContent).toContain('服务端统计口径')
    expect(container.textContent).toContain('AI 助手')
    // 空串桶展示为「其他」
    expect(container.textContent).toContain('其他')
    expect(container.textContent).toContain('1 错')
    // 样本聚合口径已删除
    expect(container.textContent).not.toContain('样本聚合')
  })

  it('模型健康卡消费 /sys/model-health 探测缓存并列出异常模型', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    mocks.listModelHealth.mockResolvedValue({
      items: [
        { modelName: 'main-chat', provider: 'volcengine', status: 'healthy', consecutiveFailures: 0, lastLatencyMs: 420, lastCheckedAt: new Date().toISOString(), lastError: null },
        { modelName: 'local-embed', provider: 'ollama', status: 'provider_unreachable', consecutiveFailures: 3, lastLatencyMs: null, lastCheckedAt: null, lastError: null },
      ],
    })
    const container = await mountOverview()

    expect(container.textContent).toContain('模型健康')
    expect(container.textContent).toContain('2 个已探测模型')
    expect(container.textContent).toContain('1 健康')
    expect(container.textContent).toContain('1 异常')
    expect(container.textContent).toContain('local-embed')
    expect(container.textContent).toContain('连续失败 3 次')
    expect(container.textContent).toContain('在模型管理查看')
  })

  it('模型健康读取失败如实显示未采集，不伪造数据', async () => {
    mocks.listTracesPage.mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 200, has_more: false, retention_limit: 5000 })
    mocks.getTraceStats.mockResolvedValue(makeStats())
    mocks.listModelHealth.mockRejectedValue(new Error('503'))
    const container = await mountOverview()
    expect(container.textContent).toContain('模型健康缓存未采集')
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
