import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TokensSummary } from '@/api/observability'

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

function summary(): TokensSummary {
  return {
    days: 7,
    totals: {
      total_tokens: 182540,
      calls: 312,
      prompt_tokens: 150740,
      completion_tokens: 31800,
      cached_tokens: 12301,
      reasoning_tokens: 0,
      cost_classification_version: 2,
      cost_usd: 0,
      cost_cny: 0.87,
      requests: 189,
      exact_cost_cny: 0.25,
      estimated_cost_cny: 0.62,
      exact_calls: 12,
      estimated_calls: 214,
      historically_estimated_calls: 214,
      unknown_cost_calls: 0,
      local_cost_calls: 86,
      local_usage_missing_calls: 0,
      unverified_price_calls: 214,
    },
    daily: [
      { day: '2026-10-29', calls: 100, prompt_tokens: 50000, completion_tokens: 12000, total_tokens: 62000, cost_usd: 0, cost_cny: 0.42 },
      { day: '2026-10-30', calls: 120, prompt_tokens: 60000, completion_tokens: 15000, total_tokens: 75000, cost_usd: 0, cost_cny: 0.45 },
    ],
    models: [
      { provider: '本地重排服务（GPU）', model: 'bge-m3', calls: 86, requests: 80, prompt_tokens: 41200, completion_tokens: 0, total_tokens: 41200, cached_tokens: 0, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0, local_cost_calls: 86 },
      { provider: 'doubao plan 专用通道', model: 'doubao-seed-2.0-mini', calls: 214, requests: 100, prompt_tokens: 96400, completion_tokens: 32000, total_tokens: 128400, cached_tokens: 12301, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0.62, estimated_cost_cny: 0.62, estimated_calls: 214, historically_estimated_calls: 214, registry_fallback_calls: 214 },
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
        cost_display_status: 'estimated',
        cost_display_source: 'official_historical_price',
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
    expect(container.textContent).toContain('精确计价')
    expect(container.textContent).toContain('¥0.25')
    expect(container.textContent).toContain('公开价估算')
    expect(container.textContent).toContain('¥0.62')
    expect(container.textContent).toContain('本地 86 次')
    expect(container.textContent).toContain('按 trace 去重')
    expect(container.textContent).toContain('本地重排服务（GPU）')
    expect(container.textContent).toContain('doubao-seed-2.0-mini')

    const chart = container.querySelector('[role="img"][aria-label="按日的Token消耗柱状图"]')
    const bars = chart?.querySelectorAll('div[style*="height"]')
    expect(bars).toHaveLength(2)
    expect(bars?.[0]?.parentElement?.className).toContain('h-32')
  })

  it('后端未提供新版计价分类时隐藏费用，避免继续展示旧金额', async () => {
    const data = summary()
    data.totals.cost_classification_version = 1
    mocks.getTokensSummary.mockResolvedValue(data)
    const container = await mountPanel()

    expect(container.textContent).toContain('计价口径未确认')
    expect(container.textContent).toContain('已隐藏金额')
    expect(container.textContent).not.toContain('¥0.25')
    expect(container.textContent).not.toContain('¥0.62')
  })

  it('切换 OCR 组件与 90 天窗口传正确参数', async () => {
    mocks.getTokensSummary.mockResolvedValue(summary())
    const container = await mountPanel()

    const ocr = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === '文字识别')
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

  it('用可追溯公开价估算 OCR 历史记录并保留小额精度', async () => {
    const data = summary()
    data.totals.calls = 222
    data.totals.missing_cost_status_calls = 222
    data.totals.exact_cost_cny = 0
    data.totals.estimated_cost_cny = 0.1238796
    data.totals.estimated_calls = 222
    data.totals.exact_calls = 0
    data.totals.subscription_cost_calls = 0
    data.totals.historically_estimated_calls = 222
    data.totals.unknown_cost_calls = 0
    data.totals.unverified_price_calls = 0
    data.totals.local_cost_calls = 0
    data.models = [{
      provider: '自定义接口', model: 'qwen-vl-ocr', calls: 222, requests: 5,
      prompt_tokens: 335082, completion_tokens: 46710, total_tokens: 381792,
      cached_tokens: 0, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0.1238796,
      exact_cost_cny: 0, estimated_cost_cny: 0.1238796,
      estimated_calls: 222, historically_estimated_calls: 222,
      unknown_cost_calls: 0,
      missing_cost_status_calls: 222,
    }]
    mocks.getTokensSummary.mockResolvedValue(data)
    mocks.getTokensCalls.mockResolvedValue({
      calls: [{
        ts: '2026-11-13T14:17:35Z', trace_id: 'ocr-trace', session_id: '',
        component: 'ocr', model: 'qwen-vl-ocr', provider: '自定义接口',
        prompt_tokens: 3829, completion_tokens: 593, total_tokens: 4422,
        cached_tokens: 0, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0.0014452,
        cost_status: '', cost_display_status: 'estimated',
        cost_display_source: 'official_historical_price',
        duration_ms: 12000, finish_reason: 'success',
      }], total: 1,
    })
    const container = await mountPanel()

    expect(container.textContent).toContain('估算 ¥0.1238796')
    expect(container.textContent).toContain('原计价状态缺失 222 次')
    expect(container.textContent).not.toContain('未知 0 次')
    const callsToggle = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('调用明细'))
    await act(async () => {
      callsToggle?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.textContent).toContain('估算 ¥0.0014452（供应商公开价）')
  })

  it('本地模型不冒充零 Token 费用，单条缺用量时提示缺失', async () => {
    mocks.getTokensSummary.mockResolvedValue(summary())
    mocks.getTokensCalls.mockResolvedValue({
      calls: [{
        ...callsPage().calls[0], component: 'embedding', model: 'bge-m3',
        provider: '本地重排服务（GPU）', total_tokens: 0,
        cost_cny: 0, cost_display_status: 'local',
        cost_display_source: 'local_billing_history',
      }], total: 1,
    })
    const container = await mountPanel()
    const toggle = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('调用明细'))
    await act(async () => {
      toggle?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('本地 ¥0.00（不含硬件、电力）；本次 Token 用量缺失')
  })

  it('未知价格不会按 ¥0.00 冒充免费', async () => {
    const data = summary()
    data.totals.calls = 2
    data.totals.total_tokens = 14
    data.totals.exact_cost_cny = 0
    data.totals.estimated_cost_cny = 0
    data.totals.estimated_calls = 0
    data.totals.exact_calls = 0
    data.totals.unknown_cost_calls = 2
    data.totals.historically_estimated_calls = 0
    data.totals.local_cost_calls = 0
    data.totals.unverified_price_calls = 0
    data.daily = []
    data.models = [{
      provider: 'ollama', model: '本地名称未映射', calls: 2, requests: 1,
      prompt_tokens: 0, completion_tokens: 14, total_tokens: 14,
      cached_tokens: 0, reasoning_tokens: 0, cost_usd: 0, cost_cny: 0,
      exact_cost_cny: 0, estimated_cost_cny: 0, unknown_cost_calls: 2,
    }]
    mocks.getTokensSummary.mockResolvedValue(data)
    const container = await mountPanel()

    expect(container.textContent).toContain('未知 2 次')
    expect(container.textContent).toContain('金额未知')
    expect(container.textContent).not.toContain('¥0.00')
  })

  it('订阅套餐用量不按每 Token 单价冒充人民币金额', async () => {
    const data = summary()
    data.totals.calls = 638
    data.totals.total_tokens = 627813
    data.totals.exact_cost_cny = 0
    data.totals.estimated_cost_cny = 0
    data.totals.unknown_cost_calls = 48
    data.totals.subscription_cost_calls = 590
    data.totals.local_cost_calls = 0
    data.totals.estimated_calls = 0
    data.totals.exact_calls = 0
    data.totals.historically_estimated_calls = 0
    data.models = [{
      provider: '火山方舟套餐', model: 'doubao-seed-2.0-mini', calls: 638,
      requests: 157, prompt_tokens: 564446, completion_tokens: 63367,
      total_tokens: 627813, cached_tokens: 0, reasoning_tokens: 0,
      cost_usd: 0, cost_cny: 0, exact_cost_cny: 0, estimated_cost_cny: 0,
      unknown_cost_calls: 48, subscription_cost_calls: 590, estimated_calls: 0,
    }]
    mocks.getTokensSummary.mockResolvedValue(data)
    mocks.getTokensCalls.mockResolvedValue({
      calls: [{
        ...callsPage().calls[0], model: 'doubao-seed-2.0-mini',
        provider: '火山方舟套餐', cost_cny: null,
        cost_status: 'unpriced', cost_display_status: 'subscription',
        pricing_source: 'subscription_plan',
        cost_display_source: 'subscription_plan',
      }],
      total: 1,
    })

    const container = await mountPanel()
    expect(container.textContent).toContain('套餐抵扣 590 次（金额未同步）')
    expect(container.textContent).toContain('金额未知 48 次')
    expect(container.textContent).not.toContain('估算 ¥')

    const callsToggle = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('调用明细'))
    await act(async () => {
      callsToggle?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container.textContent).toContain('套餐抵扣（人民币金额未同步）')
    expect(container.textContent).not.toContain('¥0.00')
  })
})
