import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const mocks = vi.hoisted(() => ({
  listTraces: vi.fn(),
  navigation: {
    pathname: '/observability/monitoring',
    params: new URLSearchParams('tab=overview'),
    router: { push: vi.fn(), replace: vi.fn() },
  },
}))

vi.mock('@/api/observability', () => ({ listTraces: mocks.listTraces }))
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
  mocks.listTraces.mockReset()
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
  it('不从历史 Trace 样本推断活跃运行数量，也不生成运行中筛选链接', async () => {
    mocks.listTraces.mockResolvedValue([makeTrace()])
    const container = await mountOverview()

    expect(mocks.listTraces).toHaveBeenCalledWith(200)
    expect(container.textContent).toContain('运行中')
    expect(container.textContent).toContain('暂不可统计')
    expect(container.textContent).toContain('BLOCKED')
    expect(container.querySelector('a[href*="status=running"]')).toBeNull()
    expect(container.textContent).toContain('没有可确认的终态记录')
    expect(container.querySelector('tbody tr')).toBeNull()
  })

  it('为部分成功单独统计并提供对应筛选入口', async () => {
    mocks.listTraces.mockResolvedValue([
      makeTrace('partial', { id: 'partial-trace', metadata: { business_outcome: 'partial' } }),
    ])
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
    mocks.listTraces.mockResolvedValue([
      ...statuses.map((status) => makeTrace(status, { id: `trace-${status}` })),
      makeTrace('success', { id: 'trace-unknown', status: undefined }),
      makeTrace('success', { id: 'future', timestamp: new Date(Date.now() + 60_000).toISOString() }),
      makeTrace('success', { id: 'outside-window', timestamp: new Date(Date.now() - 25 * 60 * 60_000).toISOString() }),
    ])

    const container = await mountOverview()
    const recordCard = container.querySelector<HTMLAnchorElement>('a[href*="status=all"]')

    expect(recordCard?.querySelector('strong')?.textContent).toBe('9')
    expect(container.querySelector('[data-trace-id="trace-running"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="future"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="outside-window"]')).toBeNull()
  })
})
