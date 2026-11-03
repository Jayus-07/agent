import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import type { TripDayV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({
  map: {
    setView: vi.fn(), fitBounds: vi.fn(), invalidateSize: vi.fn(), remove: vi.fn(),
    attributionControl: { setPrefix: vi.fn() },
  },
}))

vi.mock('leaflet', () => ({
  map: () => mocks.map,
  tileLayer: () => ({ addTo: vi.fn() }),
  layerGroup: () => ({ addTo: vi.fn(), remove: vi.fn() }),
  divIcon: vi.fn(),
  marker: () => ({
    bindTooltip() { return this },
    on() { return this },
    addTo: vi.fn(),
  }),
}))

import TravelV2Map from './TravelV2Map'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it('隐藏恢复和容器变宽后重算最新点位，切换日期及卸载时释放观察器', async () => {
  const observers: Array<{ callback: ResizeObserverCallback; disconnect: ReturnType<typeof vi.fn> }> = []
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    disconnect = vi.fn()
    constructor(callback: ResizeObserverCallback) {
      observers.push({ callback, disconnect: this.disconnect })
    }
  })
  const container = document.createElement('div')
  const root = createRoot(container)
  const day = (lat: number, lng: number) => ({
    day_id: 'day-1', items: [{ item_id: 'place-1', title: '地点', place: { lat, lng } }],
  }) as TripDayV2
  const render = async (value: TripDayV2) => {
    await act(async () => {
      root.render(createElement(TravelV2Map, {
        days: [value], activeDayIndex: 0, selectedItemId: '', selectedLocation: null, onSelectItem: () => {},
      }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
  try {
    await render(day(30, 120))
    const target = container.querySelector('[role="img"]')!
    expect(observers).toHaveLength(1)
    act(() => observers[0].callback([], {} as ResizeObserver))
    expect(mocks.map.invalidateSize).not.toHaveBeenCalled()
    Object.defineProperties(target, {
      clientWidth: { configurable: true, value: 420 },
      clientHeight: { configurable: true, value: 320 },
    })
    act(() => observers[0].callback([], {} as ResizeObserver))
    expect(mocks.map.invalidateSize).toHaveBeenCalledWith({ pan: false, animate: false })
    expect(mocks.map.fitBounds).toHaveBeenLastCalledWith([[30, 120]], {
      padding: [32, 32], maxZoom: 15, animate: false,
    })
    await render(day(31, 121))
    expect(observers[0].disconnect).toHaveBeenCalledOnce()
    act(() => observers[1].callback([], {} as ResizeObserver))
    expect(mocks.map.fitBounds).toHaveBeenLastCalledWith([[31, 121]], {
      padding: [32, 32], maxZoom: 15, animate: false,
    })
  } finally {
    act(() => root.unmount())
    container.remove()
  }
  expect(observers.at(-1)!.disconnect).toHaveBeenCalledOnce()
  expect(mocks.map.remove).toHaveBeenCalledOnce()
})
