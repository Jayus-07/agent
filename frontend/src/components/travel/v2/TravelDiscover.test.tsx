import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ searchFood: vi.fn(), addMeal: vi.fn(), selectLodging: vi.fn(), selectTrain: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  searchTravelFoodV2: mocks.searchFood,
  addTravelMealV2: mocks.addMeal,
  selectTravelLodgingV2: mocks.selectLodging,
  selectTravelTrainV2: mocks.selectTrain,
}))

import TravelDiscover from './TravelDiscover'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
})

beforeEach(() => {
  mocks.searchFood.mockReset()
  mocks.addMeal.mockReset()
  mocks.selectLodging.mockReset()
  mocks.selectTrain.mockReset()
})

describe('旅游 V2 发现页', () => {
  it('提供美食、酒店、动车真实查询入口且无 Trip 时查询不会触发行程写入', async () => {
    mocks.searchFood.mockResolvedValue({
      kind: 'food', status: 'success', source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      message: '查询完成', disclosure: '', results: [{
        selection_id: 'food-selection', merchant_id: 'merchant-1', name: '杭州小馆', address: '杭州市',
        rating: null, open_status: null, open_time_today: null, lat: null, lng: null,
        avg_cost_cny: null, source: 'amap', queried_at: '2026-10-12T00:00:00Z',
      }],
    })
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    await act(async () => { root?.render(createElement(TravelDiscover)) })

    expect(container.textContent).toContain('美食')
    expect(container.textContent).toContain('酒店')
    expect(container.textContent).toContain('动车')
    expect(container.textContent).toContain('只读查询 · 不会修改或创建行程')
    expect(container.querySelector('nav[aria-label="手机导航"] a[aria-current="page"]')?.textContent).toBe('发现')

    const city = container.querySelector('input[placeholder="输入城市，如杭州"]') as HTMLInputElement
    const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    await act(async () => {
      valueSetter?.call(city, '杭州')
      city.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => {
      container?.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.searchFood).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('杭州小馆')
    const result = [...(container.querySelectorAll('button') ?? [])].find((button) => button.textContent?.includes('杭州小馆'))
    await act(async () => { result?.click() })
    expect(container.textContent).toContain('杭州小馆')
    expect([...container.querySelectorAll('button')].some((button) => button.textContent?.includes('加入第'))).toBe(false)
    expect(mocks.addMeal).not.toHaveBeenCalled()
    expect(mocks.selectLodging).not.toHaveBeenCalled()
    expect(mocks.selectTrain).not.toHaveBeenCalled()
  })

  it('切换到酒店和动车后选中类别与查询表单保持一致', async () => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    await act(async () => { root?.render(createElement(TravelDiscover)) })

    const hotel = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('酒店'))!
    await act(async () => { hotel.click() })
    expect(hotel.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('form')).not.toBeNull()

    const train = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('动车'))!
    await act(async () => { train.click() })
    expect(train.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('input[placeholder="出发站"]')).not.toBeNull()
    expect(container.querySelector('input[placeholder="到达站"]')).not.toBeNull()
  })
})
