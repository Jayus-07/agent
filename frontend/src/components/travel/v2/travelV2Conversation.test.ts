import { afterEach, describe, expect, it } from 'vitest'
import {
  defaultTravelV2ConversationId,
  projectTravelV2TripFacts,
  parseTravelV2RestoreRevisionRequest,
  setTravelV2TripAssociation,
  travelV2ConversationIdForTrip,
  travelV2ChatHref,
  travelV2WorkspaceHref,
  travelV2WorkspaceRestoreHref,
} from './travelV2Conversation'
import type { TravelTripV2 } from '@/api/travelV2'

function projectionTrip(days: unknown[], arrangements: Record<string, unknown> = {}): TravelTripV2 {
  return {
    trip_id: 'trip-projection', title: '杭州慢游', status: 'active', revision: 1,
    document: {
      brief: { origin: '上海', destination: '杭州', start_date: '2026-10-20', day_count: 1, timezone: 'Asia/Shanghai', budget: { amount: null, currency: 'CNY' } },
      days, arrangements: { lodgings: [], intercity_trains: [], ...arrangements },
      totals: { estimated_cost: null, currency: 'CNY' },
    },
  } as unknown as TravelTripV2
}

function item(index: number, overrides: Record<string, unknown> = {}) {
  return {
    item_id: `item-${index}`, kind: 'place', activity_type: null,
    start_time: '09:00', title: `地点 ${index}`, duration_min: 60,
    place: { name: `地点 ${index}`, address: '杭州', facts: {} }, note: '',
    ...overrides,
  }
}

describe('V2 Trip 会话关联', () => {
  afterEach(() => window.localStorage.clear())

  it('只从服务端关联恢复 Trip 会话，入口链接保留已验证 ID', () => {
    const tripId = 'trip-a'
    const conversationId = defaultTravelV2ConversationId(tripId)

    expect(travelV2ConversationIdForTrip(tripId, conversationId)).toBe(conversationId)
    expect(() => travelV2ConversationIdForTrip(tripId)).toThrow(/server-verified conversation association/)
    expect(travelV2WorkspaceHref(tripId, conversationId)).toBe('/travel/itineraries/trip-a?conversation_id=travel-v2-trip-trip-a')
    expect(travelV2WorkspaceRestoreHref(tripId, conversationId, 2)).toBe('/travel/itineraries/trip-a?conversation_id=travel-v2-trip-trip-a&restore_revision=2')
    expect(travelV2ChatHref(tripId, conversationId)).toBe('/travel/chat?trip_id=trip-a&conversation_id=travel-v2-trip-trip-a')
  })

  it('只接受服务端验证的会话 ID，本地缓存和 URL 候选不作为关联依据', () => {
    setTravelV2TripAssociation('planning-conversation', 'trip-a')

    expect(() => travelV2ConversationIdForTrip('trip-a')).toThrow(/server-verified conversation association/)
    expect(travelV2ConversationIdForTrip('trip-a', 'planning-conversation')).toBe('planning-conversation')
  })

  it('大量天数、安排、车次和长文本始终严格受事实投影预算约束', () => {
    const manyDays = Array.from({ length: 80 }, (_, dayIndex) => ({
      date: '2026-10-20', title: `第 ${dayIndex + 1} 天`,
      items: Array.from({ length: 20 }, (_, itemIndex) => item(dayIndex * 20 + itemIndex, {
        title: '很长的地点名称'.repeat(30), note: '很长的备注'.repeat(40),
      })),
      legs: Array.from({ length: 20 }, (_, legIndex) => ({
        from_item_id: `item-${legIndex}`, to_item_id: `item-${legIndex + 1}`,
        selected_mode: 'walk', duration_min: 20, reliability: 'estimated',
      })),
    }))
    const arrangements = {
      lodgings: Array.from({ length: 60 }, (_, index) => ({
        merchant: { name: `酒店 ${index}`, address: '杭州市'.repeat(80) },
        check_in: '2026-10-20', check_out: '2026-10-21', nightly_price_cny: 500,
        verification: 'estimated', source: 'fixture', queried_at: new Date().toISOString(),
        booking_status: 'not_booked',
      })),
      intercity_trains: Array.from({ length: 120 }, (_, index) => ({
        train_code: `G${index}`, travel_date: '2026-10-20',
        departure_station: '上海虹桥'.repeat(60), arrival_station: '杭州东'.repeat(60),
        departure_time: '09:00', arrival_time: '10:00', duration_min: 60,
        booking_status: 'not_booked',
      })),
    }
    const facts = projectTravelV2TripFacts(projectionTrip(manyDays, arrangements))
    const truncation = facts.truncation as Record<string, unknown>

    expect(JSON.stringify(facts).length).toBeLessThanOrEqual(6500)
    expect(truncation.applied).toBe(true)
    expect(Number(truncation.omitted_days) + Number(truncation.omitted_items)
      + Number(truncation.omitted_legs) + Number(truncation.omitted_lodgings)
      + Number(truncation.omitted_intercity_trains)).toBeGreaterThan(0)
  })

  it('单日包含大量腿和安排时也会裁剪并保留遗漏标记', () => {
    const days = [{
      date: '2026-10-20', title: '单日超大行程',
      items: Array.from({ length: 500 }, (_, index) => item(index)),
      legs: Array.from({ length: 4000 }, (_, index) => ({
        from_item_id: `item-${index}`, to_item_id: `item-${index + 1}`,
        selected_mode: 'transit', duration_min: 20, reliability: 'estimated',
      })),
    }]
    const facts = projectTravelV2TripFacts(projectionTrip(days))
    const truncation = facts.truncation as Record<string, unknown>

    expect(JSON.stringify(facts).length).toBeLessThanOrEqual(6500)
    expect(truncation.applied).toBe(true)
    expect(Number(truncation.omitted_legs)).toBeGreaterThan(0)
    expect(Number(truncation.omitted_items)).toBeGreaterThan(0)
    expect((facts.arrangements as Record<string, unknown>).lodging_unarranged).toBe(false)
  })

  it('保留酒店正式报价证据并区分过期或未经核验的价格', () => {
    const arrangements = {
      lodgings: [
        {
          merchant: { name: '西湖酒店', address: '杭州' }, check_in: '2026-10-20',
          check_out: '2026-10-21', nightly_price_cny: 520, verification: 'verified',
          source: 'hotel-provider', queried_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
          booking_status: 'not_booked',
        },
        {
          merchant: { name: '未核验酒店', address: '杭州' }, check_in: '2026-10-20',
          check_out: '2026-10-21', nightly_price_cny: 300, verification: 'estimated',
          source: 'hotel-provider', queried_at: new Date().toISOString(),
          booking_status: 'not_booked',
        },
        {
          merchant: { name: '过期酒店', address: '杭州' }, check_in: '2026-10-20',
          check_out: '2026-10-21', nightly_price_cny: 400, verification: 'verified',
          source: 'hotel-provider', queried_at: new Date(Date.now() - 48 * 60 * 60 * 1000).toISOString(),
          booking_status: 'not_booked',
        },
      ],
    }
    const facts = projectTravelV2TripFacts(projectionTrip([], arrangements))
    const lodging = (facts.arrangements as { lodgings: Array<{ price_evidence: Record<string, unknown> }> }).lodgings

    expect((facts.trip as Record<string, unknown>).title).toBe('杭州慢游')
    expect(lodging.map((item) => item.price_evidence.freshness)).toEqual(['current', 'unverified', 'stale'])
    expect(lodging[0].price_evidence).toMatchObject({
      nightly_price_cny: 520, verification: 'verified', source: 'hotel-provider',
    })
    expect(lodging[0].price_evidence.queried_at).toEqual(expect.any(String))
  })

  it('英文名称不会因 token 前缀被误删，已知密钥格式仍会脱敏', () => {
    const openAiKey = ['sk', 'proj', '12345678901234567890'].join('-')
    const labelledToken = ['token', 'abc12345678901234567890'].join('=')
    const trip = projectionTrip([{
      date: '2026-10-20', title: '第一天', legs: [],
      items: [item(1, { title: `SkylineObservationDeck TokenizationArchitecture ${openAiKey} ${labelledToken}` })],
    }])
    const serialized = JSON.stringify(projectTravelV2TripFacts(trip))

    expect(serialized).toContain('SkylineObservationDeck')
    expect(serialized).toContain('TokenizationArchitecture')
    expect(serialized).not.toContain(openAiKey)
    expect(serialized).not.toContain(labelledToken)
    expect(serialized).toContain('[已过滤凭据]')
  })

  it('只把明确指定正式版本的恢复指令识别为 TRIP_RESTORE', () => {
    expect(parseTravelV2RestoreRevisionRequest('把这趟行程恢复到正式版本 v3')).toBe(3)
    expect(parseTravelV2RestoreRevisionRequest('请还原到第 2 版')).toBe(2)
    expect(parseTravelV2RestoreRevisionRequest('把西湖改到上午十点')).toBeNull()
    expect(parseTravelV2RestoreRevisionRequest('恢复一下行程')).toBeNull()
  })
})
