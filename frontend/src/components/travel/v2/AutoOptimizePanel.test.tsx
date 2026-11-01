import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  autoOptimize: vi.fn(),
  addMeal: vi.fn(),
  selectLodging: vi.fn(),
}))

vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  autoOptimizeTravelTripV2: mocks.autoOptimize,
  addTravelMealV2: mocks.addMeal,
  selectTravelLodgingV2: mocks.selectLodging,
}))

import AutoOptimizePanel from './AutoOptimizePanel'
import type { TravelTripV2 } from '@/api/travelV2'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const TRIP = {
  trip_id: 'trip-opt-1',
  title: '厦门 2 天行程',
  status: 'active',
  revision: 3,
  source_template_id: null,
  source_template_version: null,
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  document: {
    schema_version: 2,
    brief: {
      origin: '上海', destination: '厦门', timezone: 'Asia/Shanghai',
      start_date: '2026-12-01', day_count: 2,
      travelers: { adults: 2, children: 0 },
      budget: { amount: 3000, currency: 'CNY' },
      pace: 'relaxed', interests: [], requirements: [],
    },
    selections: [],
    days: [
      { day_id: 'd1', date: '2026-12-01', title: '第一天', items: [], legs: [] },
      { day_id: 'd2', date: '2026-12-02', title: '第二天', items: [], legs: [] },
    ],
    arrangements: { lodgings: [], intercity_trains: [] },
    totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
    health: { status: 'ok', issues: [] },
  },
} as unknown as TravelTripV2

describe('自动优选建议确认卡（F04）', () => {
  beforeEach(() => {
    mocks.autoOptimize.mockReset()
    mocks.addMeal.mockReset()
    mocks.selectLodging.mockReset()
  })
  afterEach(() => { vi.clearAllMocks() })

  it('生成建议后渲染建议与披露，逐条幂等选入并链式推进 revision', async () => {
    mocks.autoOptimize.mockResolvedValue({
      trip_id: 'trip-opt-1', destination: '厦门',
      suggestions: {
        meals: [
          { selection_id: 'amap:a', merchant_id: 'B001', name: '临家闽南菜', rating: 4.7,
            avg_cost_cny: 80, address: '思明区', lat: 24.48, lng: 118.12,
            open_status: null, open_time_today: null, source: 'amap',
            observed_at: '2026-11-01T08:00:00+00:00', reason: '评分最高' },
          { selection_id: 'amap:c', merchant_id: 'B003', name: '乌糖沙茶面', rating: 4.5,
            avg_cost_cny: 30, address: '思明区', lat: 24.47, lng: 118.10,
            open_status: null, open_time_today: null, source: 'amap',
            observed_at: '2026-11-01T08:00:00+00:00', reason: '评分最高' },
        ],
        lodgings: [
          { selection_id: 'ho:2', merchant_id: 'P2', name: '青旅', city: '厦门',
            address: '厦大', lat: 24.43, lng: 118.09, check_in: '2026-12-01',
            check_out: '2026-12-03', nights: 2, price_total: '240', currency: 'CNY',
            nightly_price_cny: 120, tier: 'low', availability: 'limited',
            booking_status: 'not_booked', source: 'rollinggo:mcp',
            observed_at: '2026-11-01T08:00:00+00:00', reason: '价格最低' },
        ],
      },
      disclosures: ['商户评分来自高德，可能随时间变化。'],
      apply_hint: '确认后将按既有 selections 端点逐条选入（逐条幂等）',
    })
    // 逐条返回链式 revision：3 → 4 → 5 → 6（每条成功后下一条用新 revision）
    let revision = 3
    const nextTrip = () => ({ saved: true, trip_id: 'trip-opt-1', revision: ++revision, document: TRIP.document })
    mocks.addMeal.mockImplementation(async () => nextTrip())
    mocks.selectLodging.mockImplementation(async () => nextTrip())
    const onTripSaved = vi.fn()

    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(AutoOptimizePanel, {
        trip: TRIP, disabled: false, onTripSaved, onClose: () => undefined,
      }))
      await Promise.resolve()
    })
    // 初始态：未生成建议
    expect(container.textContent).toContain('生成建议')
    expect(container.textContent).not.toContain('临家闽南菜')
    const loadButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '生成建议')!
    await act(async () => {
      loadButton.click()
      await Promise.resolve()
    })

    expect(mocks.autoOptimize).toHaveBeenCalledWith('trip-opt-1')
    expect(container.textContent).toContain('临家闽南菜')
    expect(container.textContent).toContain('乌糖沙茶面')
    expect(container.textContent).toContain('青旅')
    expect(container.textContent).toContain('商户评分来自高德，可能随时间变化。')
    expect(container.textContent).toContain('逐条选入（逐条幂等）')

    const applyButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '确认并逐条选入')!
    await act(async () => {
      applyButton.click()
      await Promise.resolve()
    })

    // 餐饮逐条：默认餐位 d1/lunch、d1/dinner；住宿 1 条；每条独立幂等键
    expect(mocks.addMeal).toHaveBeenCalledTimes(2)
    expect(mocks.addMeal.mock.calls[0][1]).toMatchObject({
      day_id: 'd1', meal_period: 'lunch', selection_id: 'amap:a',
    })
    expect(mocks.addMeal.mock.calls[0][1].merchant).toMatchObject({
      merchant_id: 'B001', source: 'amap', observed_at: '2026-11-01T08:00:00+00:00',
    })
    expect(mocks.addMeal.mock.calls[1][1]).toMatchObject({
      day_id: 'd1', meal_period: 'dinner', selection_id: 'amap:c',
    })
    expect(mocks.selectLodging).toHaveBeenCalledTimes(1)
    expect(mocks.selectLodging.mock.calls[0][1].lodging).toMatchObject({
      selection_id: 'ho:2', booking_status: 'not_booked', verification: 'unknown',
      check_in: '2026-12-01', check_out: '2026-12-03', nightly_price_cny: 120,
    })
    // 链式 revision：第 3 条调用（住宿）的 expected_revision = 5（3→4→5 之后）
    expect(mocks.selectLodging.mock.calls[0][1].expected_revision).toBe(5)
    // 完成后回传最新行程
    expect(onTripSaved).toHaveBeenCalledOnce()
    expect(onTripSaved.mock.calls[0][0].revision).toBe(6)
    expect(container.textContent).toContain('已选入')
    await act(async () => root.unmount())
  })

  it('编排失败时如实披露，已完成条目保持有效', async () => {
    mocks.autoOptimize.mockResolvedValue({
      trip_id: 'trip-opt-1', destination: '厦门',
      suggestions: {
        meals: [
          { selection_id: 'amap:a', merchant_id: 'B001', name: '临家闽南菜', rating: 4.7,
            avg_cost_cny: 80, address: null, lat: null, lng: null, open_status: null,
            open_time_today: null, source: 'amap',
            observed_at: '2026-11-01T08:00:00+00:00', reason: '评分最高' },
        ],
        lodgings: [],
      },
      disclosures: [], apply_hint: '确认后将按既有 selections 端点逐条选入（逐条幂等）',
    })
    mocks.addMeal.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const onTripSaved = vi.fn()
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(AutoOptimizePanel, {
        trip: TRIP, disabled: false, onTripSaved, onClose: () => undefined,
      }))
      await Promise.resolve()
    })
    const loadButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '生成建议')!
    await act(async () => { loadButton.click(); await Promise.resolve() })
    const applyButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '确认并逐条选入')!
    await act(async () => { applyButton.click(); await Promise.resolve() })

    expect(container.querySelector('[role="alert"]')?.textContent).toContain('选入未全部完成')
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('已完成的部分保持有效')
    expect(onTripSaved).not.toHaveBeenCalled()
    await act(async () => root.unmount())
  })
})
