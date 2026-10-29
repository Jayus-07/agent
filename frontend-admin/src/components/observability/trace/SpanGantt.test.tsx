import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { Span } from '@/types/trace'
import SpanGantt from './SpanGantt'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
})

function span({ id, ...overrides }: Partial<Span> & { id: string }): Span {
  return {
    type: 'retrieval',
    name: id,
    parent_id: null,
    status: 'success',
    start_time: '2026-10-30T02:22:04.160Z',
    duration_ms: 100,
    duration_ratio: 0,
    attributes: {},
    metrics: {},
    children: [],
    events: [],
    warnings: [],
    errors: [],
    ...overrides,
    id,
  } as Span
}

function makeSpans(): Span[] {
  const base = '2026-10-30T02:22:04.160Z'
  return [
    span({ id: 'root', name: 'RAG 智能问答', type: 'agent', duration_ms: 8100, start_time: base }),
    span({ id: 'retrieve', name: '混合检索', duration_ms: 3800, start_time: base, parent_id: 'root', metrics: { retrieved_chunks: 1 } }),
    span({
      id: 'tool',
      name: 'calculate_tool',
      type: 'tool_call',
      status: 'error',
      duration_ms: 2,
      start_time: base,
      parent_id: 'root',
      metrics: { error_code: 'SIMULATED_TIMEOUT' },
      errors: ['模拟上游响应超时'],
    }),
    span({
      id: 'llm',
      name: 'LLM生成',
      type: 'llm_call',
      duration_ms: 975,
      start_time: base,
      parent_id: 'root',
      llm_call: { model: 'doubao-seed-2.0-mini', temperature: 0, prompt_text: 'prompt 文本', response_text: 'response 文本', prompt_tokens: 851, completion_tokens: 50, cost_usd: 0 },
    }),
  ]
}

async function mount(props: { totalMs: number; uncoveredMs?: number; spans: Span[] }): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<SpanGantt spans={props.spans} totalMs={props.totalMs} uncoveredMs={props.uncoveredMs} />)
  })
  return container
}

describe('SpanGantt（Span 甘特时间线）', () => {
  it('渲染层级 span 与汇总条', async () => {
    const container = await mount({ spans: makeSpans(), totalMs: 8100 })
    expect(container.textContent).toContain('RAG 智能问答')
    expect(container.textContent).toContain('混合检索')
    expect(container.textContent).toContain('calculate_tool')
    expect(container.textContent).toContain('LLM 1')
    expect(container.textContent).toContain('工具 1')
  })

  it('点击行展开指标 / Input / HTTP 拆分', async () => {
    const container = await mount({ spans: makeSpans(), totalMs: 8100 })
    expect(container.textContent).not.toContain('SIMULATED_TIMEOUT')
    const row = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('calculate_tool'))
    await act(async () => { row?.click() })
    expect(container.textContent).toContain('SIMULATED_TIMEOUT')
    expect(container.textContent).toContain('模拟上游响应超时')
    const llmRow = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('LLM生成'))
    await act(async () => { llmRow?.click() })
    expect(container.textContent).toContain('doubao-seed-2.0-mini')
  })

  it('失败 span 的耗时标红（is-bad）', async () => {
    const container = await mount({ spans: makeSpans(), totalMs: 8100 })
    const badBars = container.querySelectorAll('.om-fallback') // 占位：实际类名断言见下
    void badBars
    const redBar = container.querySelector('span[class*="!bg-red-400"]')
    expect(redBar).toBeTruthy()
  })

  it('uncoveredMs > 0 时显示未埋点黑洞段', async () => {
    const container = await mount({ spans: makeSpans(), totalMs: 8100, uncoveredMs: 3698 })
    expect(container.textContent).toContain('未埋点黑洞')
    expect(container.textContent).toContain('3.7s')
  })

  it('uncoveredMs = 0 时不显示黑洞段', async () => {
    const container = await mount({ spans: makeSpans(), totalMs: 8100 })
    expect(container.textContent).not.toContain('未埋点黑洞')
  })
})
