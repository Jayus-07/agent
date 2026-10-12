import { describe, expect, it } from 'vitest'
import type { Itinerary } from '@/api/travel'
import { itineraryToTripDocumentV2 } from './travelV2PlannerAdapter'

const itinerary = {
  brief: {
    origin: '福州', destination: '杭州', start_date: '2026-12-01', days: 1,
    party_size: 3, adults: 2, children: 1,
    budget_cny: 4000, pace: 'relaxed', preferences: ['自然', '美食'],
    must_go: ['西湖'], avoid: ['爬山'], diet: '', lodging: '', transport: '',
  },
  days: [{
    day_index: 1, day_date: '2026-12-01', active_minutes: 180, transit_minutes: 20,
    cost_cny: 50,
    items: [
      { title: '西湖', kind: 'visit', start: '09:00', end: '10:30', minutes: 90, wait_minutes: 0, note: '湖边漫步', poi: { poi_id: 'amap:poi-1', name: '西湖', lat: 30.25, lng: 120.15, ticket_cny: 0, source: 'amap', location_status: 'verified', required: true } },
      { title: '灵隐寺', kind: 'visit', start: '10:45', end: '11:45', minutes: 60, wait_minutes: 0, note: '', poi: { poi_id: 'amap:poi-2', name: '灵隐寺', lat: 30.24, lng: 120.10, ticket_cny: 0, source: 'amap', location_status: 'verified', required: false } },
      { title: '午餐', kind: 'meal', start: '12:00', end: '13:00', minutes: 60, wait_minutes: 0, note: '附近用餐', poi: null },
    ],
    legs: [{ from_title: '西湖', to_title: '灵隐寺', minutes: 20, distance_km: 2.5, mode: 'walk', cost_cny: 0, source: 'amap:route', is_estimate: false }],
  }],
  cost: { tickets: 0, meals: 50, lodging: 0, transit: 0, total: 50 },
  warnings: ['部分营业时间待核实'],
  intercity: [],
} as unknown as Itinerary

describe('旅游 Agent 行程到 V2 文档的确定性转换', () => {
  it('保留日期、地点来源、必去项和路段事实，并把餐饮映射为 Day Item', () => {
    const document = itineraryToTripDocumentV2(itinerary, 'America/Los_Angeles')

    expect(document.schema_version).toBe(2)
    expect(document.brief).toMatchObject({
      origin: '福州', destination: '杭州', start_date: '2026-12-01',
      day_count: 1, timezone: 'America/Los_Angeles', travelers: { adults: 2, children: 1 },
      budget: { amount: 4000, currency: 'CNY' }, pace: 'relaxed',
    })
    expect(document.days[0].items[0]).toMatchObject({
      kind: 'place', title: '西湖', must_visit: true,
      place: { place_id: 'amap:poi-1', facts: { source: 'amap', verification: 'verified' } },
    })
    expect(document.days[0].items[2]).toMatchObject({ kind: 'activity', activity_type: 'meal' })
    expect(document.days[0].legs[0]).toMatchObject({
      selected_mode: 'walk', duration_min: 20, distance_m: 2500, reliability: 'verified',
    })
    expect(document.health.status).toBe('needs_attention')
  })

  it('缺少 POI 时不伪造地图地点或坐标', () => {
    const withoutPlace = {
      ...itinerary,
      days: [{ ...itinerary.days[0], items: [itinerary.days[0].items[2]], legs: [] }],
    } as Itinerary

    const document = itineraryToTripDocumentV2(withoutPlace)
    expect(document.days[0].items[0]).toMatchObject({
      kind: 'activity', activity_type: 'meal', place: null,
    })
    expect(document.days[0].legs).toHaveLength(0)
  })

  it('未知接驳耗时保持 unavailable/null，不映射为 0 分钟', () => {
    const unknownLeg = {
      ...itinerary,
      days: [{
        ...itinerary.days[0]!,
        legs: [{
          from_title: '西湖', to_title: '灵隐寺', minutes: null, distance_km: null,
          mode: 'walk', cost_cny: null, source: 'amap:route', is_estimate: false,
        }],
      }],
    } as unknown as Itinerary

    const document = itineraryToTripDocumentV2(unknownLeg)
    expect(document.days[0]?.legs[0]).toMatchObject({
      duration_min: null, distance_m: null, cost_cny: null, reliability: 'unavailable',
    })
    expect(document.health.issues.some((issue) => issue.message.includes('接驳耗时未知'))).toBe(true)
  })

  it('需求 Schema 未提供时区时提示待确认，不在映射中固定到某个城市时区', () => {
    const document = itineraryToTripDocumentV2(itinerary)
    expect(document.brief.timezone).toBeTruthy()
    expect(document.health.issues.some((issue) => issue.message.includes('未提供有效时区'))).toBe(true)
  })
})
