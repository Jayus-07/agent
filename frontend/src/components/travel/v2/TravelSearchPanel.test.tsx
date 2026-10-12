import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ searchFood: vi.fn(), searchHotel: vi.fn(), addMeal: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  searchTravelFoodV2: mocks.searchFood,
  searchTravelHotelsV2: mocks.searchHotel,
  addTravelMealV2: mocks.addMeal,
}))

import TravelSearchPanel, { type TravelSearchEvent } from './TravelSearchPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Deferred<T> {
  promise: Promise<T>
  resolve(value: T): void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((accept) => { resolve = accept })
  return { promise, resolve }
}

function foodResponse(message = '美食查询完成') {
  return {
    kind: 'food' as const, status: 'success' as const, source: 'amap',
    queried_at: '2026-10-12T00:00:00Z', message, disclosure: '', results: [],
  }
}

const trip = {
  trip_id: 'trip-a', revision: 4,
  document: {
    schema_version: 2,
    brief: { destination: '杭州', origin: '福州', start_date: '2026-10-20', day_count: 2 },
    days: [{ day_id: 'day-1' }],
  },
} as unknown as TravelTripV2

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
  vi.clearAllMocks()
})

describe('V2 搜索读请求取消', () => {
  it('Trip 页面卸载时取消 Provider 只读请求，迟到结果不再生成成功状态', async () => {
    const pending = deferred<{ kind: 'food'; status: 'success'; source: string; queried_at: string; message: string; disclosure: string; results: [] }>()
    const onSearchEvent = vi.fn<(event: TravelSearchEvent) => void>()
    const onLocationSelect = vi.fn()
    mocks.searchFood.mockReturnValue(pending.promise)
    container = document.createElement('div')
    root = createRoot(container)

    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { trip, onSearchEvent, onLocationSelect }))
    })
    const form = container.querySelector('form')!
    await act(async () => { form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })

    const requestOptions = mocks.searchFood.mock.calls[0][1] as { signal: AbortSignal }
    expect(requestOptions.signal.aborted).toBe(false)
    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying'])
    const queryId = onSearchEvent.mock.calls[0][0].queryId
    expect(queryId).toBeTruthy()

    await act(async () => { root?.unmount() })
    root = null
    expect(requestOptions.signal.aborted).toBe(true)

    pending.resolve({
      kind: 'food', status: 'success', source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      message: '迟到的 A 查询结果', disclosure: '', results: [],
    })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })

    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying', 'cancelled'])
    expect(onSearchEvent.mock.calls.map(([event]) => event.queryId)).toEqual([queryId, queryId])
    expect(onSearchEvent).not.toHaveBeenCalledWith(expect.objectContaining({ message: '迟到的 A 查询结果' }))
    expect(onLocationSelect).toHaveBeenCalledWith(null)
    expect(onLocationSelect).toHaveBeenCalledTimes(1)
    expect(mocks.addMeal).not.toHaveBeenCalled()
  })

  it('查询失败会用同一 queryId 结束 querying 状态', async () => {
    const onSearchEvent = vi.fn<(event: TravelSearchEvent) => void>()
    mocks.searchFood.mockRejectedValue(new Error('provider failed'))
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { trip, onSearchEvent }))
    })

    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying', 'business_failure'])
    expect(onSearchEvent.mock.calls[0][0].queryId).toBeTruthy()
    expect(onSearchEvent.mock.calls.map(([event]) => event.queryId)).toEqual([
      onSearchEvent.mock.calls[0][0].queryId,
      onSearchEvent.mock.calls[0][0].queryId,
    ])
    expect(mocks.addMeal).not.toHaveBeenCalled()
  })

  it('切换查询 Tab 会终结旧 query，迟到成功不能覆盖新查询', async () => {
    const pendingFood = deferred<ReturnType<typeof foodResponse>>()
    const onSearchEvent = vi.fn<(event: TravelSearchEvent) => void>()
    mocks.searchFood.mockReturnValue(pendingFood.promise)
    mocks.searchHotel.mockResolvedValue({
      kind: 'hotel', status: 'no_results', source: 'amap',
      queried_at: '2026-10-12T00:01:00Z', message: '当前酒店没有结果', disclosure: '', results: [],
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { trip, onSearchEvent }))
    })

    const form = container.querySelector('form')!
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    const oldSignal = mocks.searchFood.mock.calls[0][1].signal as AbortSignal
    const tabs = container.querySelector('nav[aria-label="行程关联查询"]')!
    const hotelTab = [...tabs.querySelectorAll('button')].find((button) => button.textContent?.trim() === '酒店')!
    await act(async () => { hotelTab.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(oldSignal.aborted).toBe(true)

    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const eventsAfterNewQuery = onSearchEvent.mock.calls.map(([event]) => event)
    expect(eventsAfterNewQuery.map((event) => event.status)).toEqual([
      'querying', 'cancelled', 'querying', 'no_results',
    ])
    expect(eventsAfterNewQuery[0].queryId).toBe(eventsAfterNewQuery[1].queryId)
    expect(eventsAfterNewQuery[2].queryId).toBe(eventsAfterNewQuery[3].queryId)
    expect(eventsAfterNewQuery[0].queryId).not.toBe(eventsAfterNewQuery[2].queryId)

    pendingFood.resolve(foodResponse('迟到的美食成功'))
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(onSearchEvent).toHaveBeenCalledTimes(4)
    expect(container.textContent).toContain('当前酒店没有结果')
    expect(container.textContent).not.toContain('迟到的美食成功')
  })

  it('Trip 切换不会取消已提交的选入写操作，也不把迟到回执归给新 Trip', async () => {
    const pending = deferred<{ saved: true; trip_id: string; revision: number; document: TravelTripV2['document'] }>()
    const onTripSaved = vi.fn()
    const result = {
      ...foodResponse('查询成功'),
      results: [{
        selection_id: 'food-selection-a', merchant_id: 'food-a', name: 'A 杭帮菜', address: '杭州',
        rating: 4.5, open_status: null, open_time_today: null, lat: 30, lng: 120,
        avg_cost_cny: 88, source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      }],
    }
    mocks.searchFood.mockResolvedValue(result)
    mocks.addMeal.mockReturnValue(pending.promise)
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => { root?.render(createElement(TravelSearchPanel, { trip, onTripSaved })) })

    const form = container.querySelector('form')!
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const resultButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('A 杭帮菜'))!
    await act(async () => { resultButton.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    const addButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('加入第 1 天'))!
    await act(async () => { addButton.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    expect(mocks.addMeal).toHaveBeenCalledOnce()
    expect(mocks.addMeal.mock.calls[0]).toHaveLength(3)
    expect(mocks.addMeal.mock.calls[0][2]).toMatch(/^[0-9a-f-]{36}$/i)
    await act(async () => { root?.unmount() })
    root = null
    expect(onTripSaved).not.toHaveBeenCalled()

    pending.resolve({ saved: true, trip_id: 'trip-a', revision: 5, document: trip.document })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })

    expect(onTripSaved).toHaveBeenCalledOnce()
    expect(onTripSaved.mock.calls[0][0].trip_id).toBe('trip-a')
    expect(onTripSaved.mock.calls[0][0].revision).toBe(5)
  })

  it('选入响应丢失时保持 UNKNOWN，重试复用原 Idempotency-Key 和 payload', async () => {
    const onTripSaved = vi.fn()
    const result = {
      ...foodResponse('查询成功'),
      results: [{
        selection_id: 'food-selection-a', merchant_id: 'food-a', name: 'A 杭帮菜', address: '杭州',
        rating: 4.5, open_status: null, open_time_today: null, lat: 30, lng: 120,
        avg_cost_cny: 88, source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      }],
    }
    mocks.searchFood.mockResolvedValue(result)
    mocks.addMeal
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ saved: true, trip_id: 'trip-a', revision: 5, document: trip.document })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => { root?.render(createElement(TravelSearchPanel, { trip, onTripSaved })) })

    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const resultButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('A 杭帮菜'))!
    await act(async () => { resultButton.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    const addButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('加入第 1 天'))!
    await act(async () => { addButton.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    expect(container.textContent).toContain('状态待核实')
    expect(container.textContent).not.toContain('保存失败，原行程保持不变')
    expect(onTripSaved).not.toHaveBeenCalled()
    const originalCall = mocks.addMeal.mock.calls[0]
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === '使用同一请求安全重试')!
    await act(async () => { retry.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    expect(mocks.addMeal).toHaveBeenCalledTimes(2)
    expect(mocks.addMeal.mock.calls[1]).toEqual(originalCall)
    expect(onTripSaved).toHaveBeenCalledOnce()
    expect(onTripSaved.mock.calls[0][0].revision).toBe(5)
  })
})
