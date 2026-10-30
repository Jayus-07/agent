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
vi.mock('@/components/observability/trace/SpanGantt', () => ({
  default: ({ totalMs, spans }: { totalMs: number; spans: unknown[] }) => (
    <div data-testid="span-gantt">SPANGANTT({totalMs}ms,{spans.length} spans)</div>
  ),
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
    workflow_name: 'agent',
    workflow_kind: 'langgraph_workflow',
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

describe('Trace 对比页（四问风格重构版）', () => {
  it('少于 2 条 id 显示引导而非空对比', async () => {
    navigation.params = new URLSearchParams({ ids: 'only-one' })
    const container = await mountPage()

    expect(container.textContent).toContain('至少需要 2 条 Trace')
    expect(container.textContent).not.toContain('读取失败/不存在')
  })

  it('并排渲染双栏摘要（徽标/指标/SpanGantt 实例）并下钻详情', async () => {
    const withSpan = fakeTrace('t-good', {
      duration_ms: 800,
      spans: [{ id: 'root', parent_id: null, metrics: {} } as TraceRecord['spans'][number]],
    })
    getTraceDetail.mockImplementation(async (id: string) =>
      id === 't-good' ? withSpan : null)

    navigation.params = new URLSearchParams({ ids: 't-good,t-missing' })
    const container = await mountPage()
    await act(async () => { await Promise.resolve() })

    // 双栏：一条正常 + 一条读取失败
    expect(container.textContent).toContain('读取失败/不存在')
    expect(container.textContent).toContain('800 ms')
    expect(container.querySelector('a[href="/observability/traces/t-good"]')).toBeTruthy()
    // runKind 徽标（langgraph_workflow + workflow_name=agent → Agent 图）
    expect(container.textContent).toContain('Agent 图')
    // SpanGantt 实例（仅可读 trace 有）
    expect(container.querySelectorAll('[data-testid="span-gantt"]').length).toBe(1)
    // 空 span 的 trace 如实提示（missing 栏不渲染 gantt）
    expect(container.textContent).not.toContain('本 Trace 无 Span 记录')
  })

  it('恰两条可读时对耗时/Token/费用做差异高亮', async () => {
    const a = fakeTrace('t-a', { duration_ms: 800, usage: { total_tokens: 100 }, cost_cny: 0.01 })
    const b = fakeTrace('t-b', { duration_ms: 2400, usage: { total_tokens: 1300 }, cost_cny: 0.13 })
    getTraceDetail.mockImplementation(async (id: string) => (id === 't-a' ? a : b))

    navigation.params = new URLSearchParams({ ids: 't-a,t-b' })
    const container = await mountPage()
    await act(async () => { await Promise.resolve() })

    // 双向差值提示：低者 emerald、高者 amber
    expect(container.textContent).toContain('耗时 −1.60 s')
    expect(container.textContent).toContain('耗时 +1.60 s')
    expect(container.textContent).toContain('Token +1,200')
    expect(container.textContent).toContain('费用 +¥0.12')
    expect(container.querySelectorAll('.text-amber-600').length).toBeGreaterThanOrEqual(3)
    expect(container.querySelectorAll('.text-emerald-600').length).toBeGreaterThanOrEqual(3)
  })

  it('空值如实显示未采集，不用 0 伪造', async () => {
    const a = fakeTrace('t-a', { usage: {}, cost_cny: undefined, model: { name: '', provider: '' } })
    const b = fakeTrace('t-b', { duration_ms: 0 })
    getTraceDetail.mockImplementation(async (id: string) => (id === 't-a' ? a : b))

    navigation.params = new URLSearchParams({ ids: 't-a,t-b' })
    const container = await mountPage()
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('未采集')
    // 空 span 的 trace 给出采集链未覆盖提示
    expect(container.textContent).toContain('本 Trace 无 Span 记录')
  })
})
