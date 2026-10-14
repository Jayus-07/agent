import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TravelTripV2 } from '@/api/travelV2'
import { transitionTravelTripWrite, type PendingTravelTripWrite, type TravelTripWriteTransition } from './travelV2WriteState'

const mocks = vi.hoisted(() => ({ searchFood: vi.fn(), searchHotel: vi.fn(), searchTrain: vi.fn(), addMeal: vi.fn(), selectHotel: vi.fn(), selectTrain: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  searchTravelFoodV2: mocks.searchFood,
  searchTravelHotelsV2: mocks.searchHotel,
  searchTravelTrainsV2: mocks.searchTrain,
  addTravelMealV2: mocks.addMeal,
  selectTravelLodgingV2: mocks.selectHotel,
  selectTravelTrainV2: mocks.selectTrain,
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
    expect(onSearchEvent.mock.calls[0][0]).not.toHaveProperty('source')
    expect(onSearchEvent.mock.calls[0][0]).not.toHaveProperty('queriedAt')
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
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('source')
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('queriedAt')
    expect(onSearchEvent).not.toHaveBeenCalledWith(expect.objectContaining({ message: '迟到的 A 查询结果' }))
    expect(onLocationSelect).toHaveBeenCalledWith(null)
    expect(onLocationSelect).toHaveBeenCalledTimes(1)
    expect(mocks.addMeal).not.toHaveBeenCalled()
  })

  it('客户端超时展示网络超时终态，不伪造来源或查询时间', async () => {
    const onSearchEvent = vi.fn<(event: TravelSearchEvent) => void>()
    mocks.searchFood.mockRejectedValue(new Error('Request timeout'))
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { onSearchEvent }))
    })

    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('查询请求超时')
    expect(container.querySelector('button[type="submit"]')?.textContent).toContain('查询')
    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying', 'network_timeout'])
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('source')
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('queriedAt')
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

    expect(onSearchEvent.mock.calls.map(([event]) => event.status)).toEqual(['querying', 'request_error'])
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('source')
    expect(onSearchEvent.mock.calls[1][0]).not.toHaveProperty('queriedAt')
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
    expect(eventsAfterNewQuery[0]).not.toHaveProperty('source')
    expect(eventsAfterNewQuery[0]).not.toHaveProperty('queriedAt')
    expect(eventsAfterNewQuery[3]).toMatchObject({
      source: 'amap',
      queriedAt: '2026-10-12T00:01:00Z',
    })
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
    let pendingWrite: PendingTravelTripWrite | null = null
    const onTripWriteTransition = vi.fn((event: TravelTripWriteTransition) => {
      const transition = transitionTravelTripWrite(pendingWrite, event)
      if (transition.accepted) pendingWrite = transition.current
      return transition.accepted
    })
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
    await act(async () => { root?.render(createElement(TravelSearchPanel, { trip, onTripSaved, onTripWriteTransition })) })

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
    expect(pendingWrite).toMatchObject({ kind: 'selection', status: 'UNKNOWN' })
    expect(onTripWriteTransition({
      kind: 'selection', tripId: trip.trip_id, signature: 'different-selection', key: 'new-selection-key', status: 'VALIDATING',
    })).toBe(false)
    const originalCall = mocks.addMeal.mock.calls[0]
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === '使用同一请求安全重试')!
    await act(async () => { retry.dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    expect(mocks.addMeal).toHaveBeenCalledTimes(2)
    expect(mocks.addMeal.mock.calls[1]).toEqual(originalCall)
    expect(onTripSaved).toHaveBeenCalledOnce()
    expect(onTripSaved.mock.calls[0][0].revision).toBe(5)
    expect(pendingWrite).toBeNull()
    expect(onTripWriteTransition.mock.calls
      .filter(([event]) => event.key === originalCall[2])
      .map(([event]) => event.status)).toEqual(['VALIDATING', 'SAVING', 'UNKNOWN', 'VALIDATING', 'SAVING', 'SAVED'])
  })

  it('HTTP 200 的 business_failure 按后端状态展示，不显示查询成功', async () => {
    mocks.searchFood.mockResolvedValue({
      kind: 'food',
      status: 'business_failure',
      source: 'actual-provider',
      queried_at: '2026-10-12T03:04:00Z',
      message: '数据源未能完成查询。',
      disclosure: '',
      results: [],
    })
    container = document.createElement('div')
    root = createRoot(container)

    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { onSearchEvent: vi.fn() }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('查询失败')
    expect(container.textContent).toContain('actual-provider')
    expect(container.textContent).toContain('数据源未能完成查询。')
    expect(container.textContent).not.toContain('查询成功')
    expect(mocks.addMeal).not.toHaveBeenCalled()
  })

  it.each([
    ['no_results', '无结果', 'status'],
    ['network_timeout', '网络超时', 'alert'],
    ['provider_unavailable', '数据源不可用', 'alert'],
    ['disabled', '能力未启用', 'alert'],
  ] as const)('后端 %s 状态按响应展示实际来源和时间', async (status, label, role) => {
    const onSearchEvent = vi.fn<(event: TravelSearchEvent) => void>()
    mocks.searchFood.mockResolvedValue({
      kind: 'food',
      status,
      source: 'actual-provider-id',
      queried_at: '2026-10-12T03:04:00Z',
      message: '服务端查询终态。',
      disclosure: '',
      results: [],
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { onSearchEvent }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain(label)
    expect(container.textContent).toContain('actual-provider-id')
    expect(container.textContent).toContain('服务端查询终态。')
    expect(container.querySelector(`[role="${role}"]`)?.textContent).toContain(label)
    expect(onSearchEvent.mock.calls[1][0]).toMatchObject({
      status,
      source: 'actual-provider-id',
      queriedAt: '2026-10-12T03:04:00Z',
    })
    expect(mocks.addMeal).not.toHaveBeenCalled()
    expect(mocks.selectHotel).not.toHaveBeenCalled()
    expect(mocks.selectTrain).not.toHaveBeenCalled()
  })

  it('有 Trip 查询成功但未选中候选时不写入，也不变更 Revision', async () => {
    const onTripSaved = vi.fn()
    mocks.searchFood.mockResolvedValue({
      ...foodResponse(),
      results: [{
        selection_id: 'food-candidate', merchant_id: 'merchant-1', name: '待选餐厅', address: '杭州',
        rating: null, open_status: null, open_time_today: null, lat: null, lng: null,
        avg_cost_cny: null, source: 'actual-provider', queried_at: '2026-10-12T00:00:00Z',
      }],
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { trip, onTripSaved }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('待选餐厅')
    expect(container.textContent).toContain('行程 v4')
    expect(container.textContent).not.toContain('加入第 1 天')
    expect(onTripSaved).not.toHaveBeenCalled()
    expect(mocks.addMeal).not.toHaveBeenCalled()
    expect(mocks.selectHotel).not.toHaveBeenCalled()
    expect(mocks.selectTrain).not.toHaveBeenCalled()
  })

  it.each([
    ['food', 'searchFood'],
    ['hotel', 'searchHotel'],
    ['train', 'searchTrain'],
  ] as const)('无 Trip 的 %s 查询只读，不创建行程或 Revision', async (kind, mockName) => {
    const searchMock = mocks[mockName]
    searchMock.mockResolvedValue({
      kind,
      status: 'no_results',
      source: 'actual-provider',
      queried_at: '2026-10-12T03:04:00Z',
      message: '没有结果。',
      disclosure: '',
      results: [],
    })
    const onTripSaved = vi.fn()
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { initialKind: kind, onTripSaved }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(searchMock).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('没有结果。')
    expect(container.textContent).toContain('只读查询')
    expect(onTripSaved).not.toHaveBeenCalled()
    expect(mocks.addMeal).not.toHaveBeenCalled()
    expect(mocks.selectHotel).not.toHaveBeenCalled()
    expect(mocks.selectTrain).not.toHaveBeenCalled()
  })

  it('无 Trip 搜索只展示候选，不提供写入行程的操作', async () => {
    mocks.searchFood.mockResolvedValue({
      ...foodResponse(),
      results: [{
        selection_id: 'food-selection',
        merchant_id: 'merchant-1',
        name: '只读候选餐厅',
        address: '杭州市',
        rating: null,
        open_status: null,
        open_time_today: null,
        lat: null,
        lng: null,
        avg_cost_cny: null,
        source: 'backend-source',
        queried_at: '2026-10-12T00:00:00Z',
      }],
    })
    container = document.createElement('div')
    root = createRoot(container)

    await act(async () => {
      root?.render(createElement(TravelSearchPanel))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('只读查询 · 不会修改或创建行程')
    expect(container.textContent).toContain('只读候选餐厅')
    expect(container.textContent).toContain('营业状态：未知')
    expect(container.textContent).not.toContain('加入第')
    expect(container.textContent).not.toContain('选为本次行程住宿')
    expect(container.textContent).not.toContain('使用该车次')
    expect(mocks.addMeal).not.toHaveBeenCalled()
  })

  it('酒店地点候选明确显示房态和每晚价格未知', async () => {
    mocks.searchHotel.mockResolvedValue({
      kind: 'hotel',
      status: 'success',
      source: 'real-hotel-source',
      queried_at: '2026-10-12T04:05:00Z',
      message: '查询完成。',
      disclosure: '当前响应不含库存。',
      results: [{
        selection_id: 'hotel-selection',
        merchant_id: 'hotel-1',
        name: '西湖酒店',
        address: '杭州市',
        rating: null,
        open_status: null,
        open_time_today: null,
        lat: 30.2,
        lng: 120.1,
        avg_cost_cny: null,
        source: 'real-hotel-source',
        queried_at: '2026-10-12T04:05:00Z',
        room_availability: 'unknown',
        nightly_price_cny: null,
      }],
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { initialKind: 'hotel', trip }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const resultButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('西湖酒店'))!
    await act(async () => {
      resultButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(container.textContent).toContain('房态：未知')
    expect(container.textContent).toContain('每晚房价：未知')
    expect(container.textContent).toContain('尚未预订')
    expect(container.textContent).toContain('real-hotel-source')
    expect(container.textContent).not.toContain('已可订')
  })

  it('动车详情使用实际来源与验证状态，未知余票和票价不伪造', async () => {
    mocks.searchTrain.mockResolvedValue({
      kind: 'train',
      status: 'success',
      source: 'aggregator-x',
      queried_at: '2026-10-12T04:05:00Z',
      message: '查询完成。',
      disclosure: '车次结果可能延迟。',
      results: [{
        selection_id: 'train-selection',
        train_code: 'G1',
        travel_date: '2026-10-20',
        departure_station: '福州',
        arrival_station: '杭州',
        departure_time: '08:00',
        arrival_time: '12:00',
        duration_min: 240,
        duration: '4小时',
        tickets: {},
        fares: {},
        ticket_source: 'rail-source-x',
        fare_source: null,
        queried_at: '2026-10-12T03:55:00Z',
        verification: 'unknown',
      }],
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root?.render(createElement(TravelSearchPanel, { initialKind: 'train', trip }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const resultButton = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('G1'))!
    await act(async () => {
      resultButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(container.textContent).toContain('车次信息：未知')
    expect(container.textContent).toContain('余票待核实')
    expect(container.textContent).toContain('票价待核实')
    expect(container.textContent).toContain('rail-source-x')
    expect(container.textContent).toContain('票价来源：未知')
    expect(container.textContent).toContain('尚未购票')
    expect(container.textContent).not.toContain('12306 聚合源')
  })
})
