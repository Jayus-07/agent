import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getTokensSummary: vi.fn(),
  getTokensCalls: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), show: vi.fn() },
}))

vi.mock('@/api/observability', () => ({
  getTokensSummary: mocks.getTokensSummary,
  getTokensCalls: mocks.getTokensCalls,
}))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => mocks.toast }))

import TokensUsagePanel from './TokensUsagePanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.getTokensSummary.mockReset()
  mocks.getTokensCalls.mockReset()
  Object.values(mocks.toast).forEach((fn) => fn.mockClear())
})

function summary() {
  return {
    days: 7,
    totals: {
      total_tokens: 182540,
      calls: 312,
      prompt_tokens: 150740,
      completion_tokens: 31800,
      cached_tokens: 12301,
      reasoning_tokens: 0,
      cost_cny: 0.87,
      requests: 189,
    },
    daily: [
      { day: '2026-10-29', calls: 100, prompt_tokens: 50000, completion_tokens: 12000, total_tokens: 62000, cost_usd: 0, cost_cny: 0.42 },
      { day: '2026-10-30', calls: 120, prompt_tokens: 60000, completion_tokens: 15000, total_tokens: 75000, cost_usd: 0, cost_cny: 0.45 },
    ],
    models: [
      { provider: '本地重排服务（GPU）', model: 'bge-m3', calls: 86, requests: 80, prompt_tokens: 41200, completion_tokens: 0, total_tokens: 41200, cached_tokens: 0, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0 },
      { provider: 'doubao plan 专用通道', model: 'doubao-seed-2.0-mini', calls: 214, requests: 100, prompt_tokens: 96400, completion_tokens: 32000, total_tokens: 128400, cached_tokens: 12301, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0.62 },
    ],
  }
}

function callsPage() {
  return {
    calls: [
      {
        ts: '2026-10-30T02:22:16.622Z',
        trace_id: '7166b52fa352',
        session_id: 's1',
        component: 'llm',
        model: 'doubao-seed-2.0-mini',
        provider: 'doubao plan 专用通道',
        prompt_tokens: 851,
        completion_tokens: 50,
        total_tokens: 901,
        cached_tokens: 0,
        reasoning_tokens: 0,
        cost_usd: 0,
        cost_cny: 0.0003,
        duration_ms: 975,
      },
    ],
    total: 312,
  }
}

async function mountPanel(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<TokensUsagePanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('TokensUsagePanel（Token 用量重构版）', () => {
  it('默认 7 天窗口请求 summary，5 卡与按模型细分渲染', async () => {
    mocks.getTokensSummary.mockResolvedValue(summary())
    const container = await mountPanel()

    expect(mocks.getTokensSummary).toHaveBeenCalledWith(7, undefined)
    expect(container.textContent).toContain('182,540')
    expect(container.textContent).toContain('含缓存命中 12,301')
    expect(container.textContent).toContain('¥0.87')
    expect(container.textContent).toContain('按 trace 去重')
    expect(container.textContent).toContain('本地重排服务（GPU）')
    expect(container.textContent).toContain('doubao-seed-2.0-mini')
  })

  it('切换 OCR 组件与 90 天窗口传正确参数', async () => {
    mocks.getTokensSummary.mockResolvedValue(summary())
    const container = await mountPanel()

    const ocr = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'OCR')
    await act(async () => {
      ocr?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const d90 = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === '近 90 天')
    await act(async () => {
      d90?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.getTokensSummary).toHaveBeenLastCalledWith(90, 'ocr')
  })

  it('展开调用明细拉取分页数据并渲染 Trace 跳转', async () => {
    mocks.getTokensSummary.mockResolvedValue(summary())
    mocks.getTokensCalls.mockResolvedValue(callsPage())
    const container = await mountPanel()

    const toggle = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('调用明细'))
    await act(async () => {
      toggle?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.getTokensCalls).toHaveBeenCalledWith(7, expect.objectContaining({ limit: 20, offset: 0 }))
    expect(container.textContent).toContain('7166b5')
    const link = container.querySelector('a[href*="/observability/traces/7166b52fa352"]')
    expect(link).toBeTruthy()
  })
})
