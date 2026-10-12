import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import type { TripDayV2 } from '@/api/travelV2'
import TravelV2Map from './TravelV2Map'
import { getTravelMapPoints, isValidMapCoordinate, travelMapPointColor } from './travelV2MapData'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function makeDay(items: unknown[]): TripDayV2 {
  return { day_id: 'day-1', date: null, title: '第 1 天', items } as unknown as TripDayV2
}

describe('TravelV2Map 点位可信度与空态', () => {
  it('只接受有效经纬度，缺少 verification 按未知处理', () => {
    const day = makeDay([
      {
        item_id: 'verified',
        title: '已核验地点',
        place: { lat: 30.2, lng: 120.1, facts: { verification: 'verified', source: 'provider-a' } },
      },
      {
        item_id: 'estimated',
        title: '估算地点',
        place: { lat: 31, lng: 121, facts: { verification: 'estimated', source: 'provider-b' } },
      },
      {
        item_id: 'unknown',
        title: '未知地点',
        place: { lat: 32, lng: 122, facts: { source: 'provider-c' } },
      },
      {
        item_id: 'invalid',
        title: '无效地点',
        place: { lat: 95, lng: 200, facts: { verification: 'verified' } },
      },
      {
        item_id: 'not-finite',
        title: '非数字地点',
        place: { lat: Number.NaN, lng: 120, facts: { verification: 'verified' } },
      },
    ])

    const points = getTravelMapPoints(day, null)

    expect(points.map((point) => [point.id, point.verification])).toEqual([
      ['verified', 'verified'],
      ['estimated', 'estimated'],
      ['unknown', 'unknown'],
    ])
    expect(points.every((point) => !point.candidate)).toBe(true)
    expect(points.map((point) => travelMapPointColor(point.verification))).toEqual([
      '#2878f5',
      '#b96818',
      '#667b94',
    ])
    expect(isValidMapCoordinate(-90, -180)).toBe(true)
    expect(isValidMapCoordinate(90, 180)).toBe(true)
    expect(isValidMapCoordinate(Number.POSITIVE_INFINITY, 120)).toBe(false)
    expect(isValidMapCoordinate(30, 181)).toBe(false)
  })

  it('查询候选使用独立标识，不并入已保存行程地点', () => {
    const day = makeDay([{
      item_id: 'saved-place',
      title: '正式行程地点',
      place: { lat: 30.2, lng: 120.1, facts: { verification: 'verified', source: 'saved-source' } },
    }])
    const points = getTravelMapPoints(day, {
      name: '临时查询候选',
      lat: 30.25,
      lng: 120.15,
      source: 'query-source',
    })

    expect(points).toHaveLength(2)
    expect(points[0]).toMatchObject({ id: 'saved-place', candidate: false, verification: 'verified' })
    expect(points[1]).toMatchObject({
      id: 'search-result',
      name: '临时查询候选',
      candidate: true,
      verification: 'unknown',
      source: 'query-source',
    })
    expect(getTravelMapPoints(day, { name: '无效候选', lat: 91, lng: 120 })).toHaveLength(1)
  })

  it('没有可用点位时展示准确空态，不采用固定城市定位', () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    act(() => root.render(createElement(TravelV2Map, {
      day: makeDay([]),
      selectedItemId: '',
      selectedLocation: null,
      onSelectItem: () => undefined,
    })))

    expect(container.textContent).toContain('这一天还没有行程地点')
    expect(container.textContent).not.toContain('杭州')
    expect(container.querySelector('.leaflet-container')).toBeNull()

    act(() => root.unmount())
  })
})
