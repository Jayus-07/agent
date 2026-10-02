import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const listAgentTraces = vi.hoisted(() => vi.fn().mockResolvedValue([]))
const getAgentTraceStats = vi.hoisted(() => vi.fn().mockResolvedValue({
  total: 0,
  error_count: 0,
  timeout_count: 0,
  avg_duration_ms: 0,
  p95_duration_ms: 0,
}))
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), show: vi.fn() }))

vi.mock('@/lib/observability/source', () => ({ listAgentTraces, getAgentTraceStats }))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => toast }))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => <a href={href} {...props}>{children}</a>,
}))
vi.mock('@/components/observability/trace/TraceBreadcrumb', () => ({ default: () => <div /> }))
vi.mock('@/components/observability/trace/StatsBar', () => ({ default: () => <div /> }))
vi.mock('@/components/observability/trace/CSQualityCard', () => ({ default: () => <div /> }))
vi.mock('@/components/observability/trace/TraceInsightsPanel', () => ({
  DURATION_BUCKETS: [],
  default: () => <div />,
}))
vi.mock('@/components/observability/trace/TraceRow', () => ({
  VALID_COL_KEYS: ['status', 'question', 'duration', 'usage', 'time', 'actions'],
  default: () => <tr />,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

import TracesPanel from './TracesPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  listAgentTraces.mockClear()
  getAgentTraceStats.mockClear()
  window.history.replaceState({}, '', '/')
})

describe('TracesPanel', () => {
  it('从 URL 读取 has_tool 下钻参数并透传给 Trace 查询', async () => {
    window.history.replaceState({}, '', '/observability/monitoring?tab=traces&has_tool=sql.query')
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<TracesPanel />))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(listAgentTraces).toHaveBeenCalledWith('sql.query')
    expect(container.textContent).toContain('当前筛选条件下无 trace')
  })
})
