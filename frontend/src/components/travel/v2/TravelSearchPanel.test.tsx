import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ searchFood: vi.fn(), addMeal: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  searchTravelFoodV2: mocks.searchFood,
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

const trip = {
  trip_id: 'trip-a', revision: 4,
  document: {
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

    await act(async () => { root?.unmount() })
    root = null
    expect(requestOptions.signal.aborted).toBe(true)

    pending.resolve({
      kind: 'food', status: 'success', source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      message: '迟到的 A 查询结果', disclosure: '', results: [],
    })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })

    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying'])
    expect(onSearchEvent).not.toHaveBeenCalledWith(expect.objectContaining({ message: '迟到的 A 查询结果' }))
    expect(onLocationSelect).toHaveBeenCalledWith(null)
    expect(onLocationSelect).toHaveBeenCalledTimes(1)
  })

  it('Trip 切换不会取消已提交的选入写操作，也不把迟到回执归给新 Trip', async () => {
    const pending = deferred<{ saved: true; revision: number; document: TravelTripV2['document'] }>()
    const onTripSaved = vi.fn()
    const result = {
      kind: 'food' as const, status: 'success' as const, source: 'amap',
      queried_at: '2026-10-12T00:00:00Z', message: '查询成功', disclosure: '',
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
    expect(mocks.addMeal.mock.calls[0]).toHaveLength(2)
    await act(async () => { root?.unmount() })
    root = null
    expect(onTripSaved).not.toHaveBeenCalled()

    pending.resolve({ saved: true, revision: 5, document: trip.document })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })

    expect(onTripSaved).toHaveBeenCalledOnce()
    expect(onTripSaved.mock.calls[0][0].trip_id).toBe('trip-a')
    expect(onTripSaved.mock.calls[0][0].revision).toBe(5)
  })
})
