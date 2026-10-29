import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const mocks = vi.hoisted(() => ({
  listTracesPage: vi.fn(),
  listActiveTraces: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), show: vi.fn() },
  navigation: {
    pathname: '/observability/monitoring',
    params: new URLSearchParams(),
    router: { push: vi.fn(), replace: vi.fn() },
  },
}))

vi.mock('@/api/observability', () => ({
  listTracesPage: mocks.listTracesPage,
  listActiveTraces: mocks.listActiveTraces,
}))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => mocks.toast }))
vi.mock('next/link', () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }))
vi.mock('@/components/observability/trace/TraceBreadcrumb', () => ({ default: () => <div /> }))
vi.mock('next/navigation', () => ({
  usePathname: () => mocks.navigation.pathname,
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))

import TraceListPanel from './TraceListPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.listTracesPage.mockReset().mockResolvedValue({ traces: [], total: 0, offset: 0, limit: 20, has_more: false, retention_limit: 5000 })
  mocks.listActiveTraces.mockReset().mockResolvedValue([])
  Object.values(mocks.toast).forEach((fn) => fn.mockClear())
  mocks.navigation.params = new URLSearchParams()
  mocks.navigation.router.push.mockClear()
  mocks.navigation.router.replace.mockClear()
  vi.restoreAllMocks()
})

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

async function mountPanel(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<TraceListPanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('TraceListPanel（执行记录重构版）', () => {
  it('深链参数映射为服务端筛选与分页', async () => {
    mocks.navigation.params = new URLSearchParams('tab=traces&range=6h&status=error&workflow=rag_agent&has_tool=calculate_tool&page=2&pageSize=50')
    mocks.listTracesPage.mockResolvedValue({ traces: [makeTrace('t1')], total: 60, offset: 50, limit: 50, has_more: true, retention_limit: 5000 })
    const container = await mountPanel()

    expect(mocks.listTracesPage).toHaveBeenCalledWith(expect.objectContaining({
      status: 'error',
      workflowName: 'rag_agent',
      hasTool: 'calculate_tool',
      limit: 50,
      offset: 50,
      excludeRunning: true,
    }))
    expect(container?.querySelector('[data-trace-id="t1"]')).toBeTruthy()
  })

  it('只看根 Trace 默认隐藏子任务，关闭开关后显示', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [makeTrace('root-1'), makeTrace('child-1', { parent_id: 'root-1', workflow_name: 'rag_agent' })],
      total: 2, offset: 0, limit: 20, has_more: false, retention_limit: 5000,
    })
    const container = await mountPanel()

    expect(container.querySelector('[data-trace-id="child-1"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="root-1"]')).toBeTruthy()
    const toggle = container.querySelector('input[aria-label="只看根 Trace"]') as HTMLInputElement
    expect(toggle.checked).toBe(true)
    await act(async () => {
      toggle.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.querySelector('[data-trace-id="child-1"]')).toBeTruthy()
  })

  it('运行中筛选走授权活跃端点且不查留存列表', async () => {
    mocks.navigation.params = new URLSearchParams('tab=traces&status=running')
    mocks.listActiveTraces.mockResolvedValue([makeTrace('live-1', { status: 'running' })])
    const container = await mountPanel()

    expect(mocks.listActiveTraces).toHaveBeenCalledTimes(1)
    expect(mocks.listTracesPage).not.toHaveBeenCalled()
    expect(container.querySelector('[data-trace-id="live-1"]')).toBeTruthy()
  })

  it('首次读取失败显示错误态而非伪造空列表，重试可恢复', async () => {
    mocks.listTracesPage.mockRejectedValueOnce({ status: 503 })
    const container = await mountPanel()
    expect(container.textContent).toContain('执行记录暂不可用')

    mocks.listTracesPage.mockResolvedValue({ traces: [makeTrace('ok-1')], total: 1, offset: 0, limit: 20, has_more: false, retention_limit: 5000 })
    const retry = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('重试读取'))
    await act(async () => {
      retry?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.querySelector('[data-trace-id="ok-1"]')).toBeTruthy()
  })

  it('勾选两条出现对比条并携带 ids 跳转对比页', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [makeTrace('cmp-a'), makeTrace('cmp-b'), makeTrace('cmp-c')],
      total: 3, offset: 0, limit: 20, has_more: false, retention_limit: 5000,
    })
    const container = await mountPanel()

    const checks = Array.from(container.querySelectorAll('input[aria-label^="选择 "]')) as HTMLInputElement[]
    await act(async () => {
      checks[0].click()
      checks[1].click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.textContent).toContain('已选 2 条')
    const go = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('对比（上限 4 条）'))
    await act(async () => {
      go?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.navigation.router.push).toHaveBeenCalledWith(
      expect.stringContaining('/observability/traces/compare?ids=cmp-a%2Ccmp-b'),
    )
  })

  it('显示问题摘要默认开启，关闭后隐藏问题文本', async () => {
    mocks.listTracesPage.mockResolvedValue({
      traces: [makeTrace('q1', { question: '企业订单的发货时效是怎么规定的' })],
      total: 1, offset: 0, limit: 20, has_more: false, retention_limit: 5000,
    })
    const container = await mountPanel()
    expect(container.textContent).toContain('企业订单的发货时效是怎么规定的')

    const toggle = container.querySelector('input[aria-label="显示问题摘要"]') as HTMLInputElement
    await act(async () => {
      toggle.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.textContent).not.toContain('企业订单的发货时效是怎么规定的')
  })
})
