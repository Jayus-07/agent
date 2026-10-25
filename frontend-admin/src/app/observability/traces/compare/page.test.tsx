import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const getTraceDetail = vi.hoisted(() => vi.fn())
const navigation = vi.hoisted(() => ({ params: new URLSearchParams() }))

vi.mock('@/api/observability', () => ({ getTraceDetail }))
vi.mock('next/navigation', () => ({
  useSearchParams: () => navigation.params,
}))
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/observability/trace/TraceBreadcrumb', () => ({
  default: () => <div />,
}))

import ComparePage from './page'

function fakeTrace(id: string, overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    id,
    request_id: '',
    timestamp: '2026-10-25T07:00:00Z',
    session_id: 's1',
    question: 'q',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 1200,
    model: { name: 'glm-4.7', provider: 'zhipu' },
    usage: { total_tokens: 100 },
    cost: {},
    cost_cny: 0.01,
    error: {},
    metadata: {},
    spans: [],
    status: 'success',
    ...overrides,
  } as TraceRecord
}

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

async function mountPage(): Promise<HTMLElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })
  await act(async () => {
    root.render(<ComparePage />)
    await Promise.resolve()
  })
  return container
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
  navigation.params = new URLSearchParams()
})

describe('Trace 对比页', () => {
  it('少于 2 条 id 显示引导而非空表', async () => {
    navigation.params = new URLSearchParams({ ids: 'only-one' })
    const container = await mountPage()

    expect(container.textContent).toContain('至少需要 2 条 Trace')
    expect(container.querySelector('table')).toBeNull()
  })

  it('并排渲染对比指标并下钻详情', async () => {
    getTraceDetail.mockImplementation(async (id: string) =>
      id === 't-good' ? fakeTrace('t-good', { duration_ms: 800, status: 'success' }) : null)

    navigation.params = new URLSearchParams({ ids: 't-good,t-missing' })
    const container = await mountPage()
    await act(async () => { await Promise.resolve() })

    expect(container.querySelectorAll('tbody tr').length).toBeGreaterThan(4)
    expect(container.textContent).toContain('800 ms')
    expect(container.textContent).toContain('读取失败/不存在')
    expect(container.querySelector('a[href="/observability/traces/t-good"]')).toBeTruthy()
  })
})
