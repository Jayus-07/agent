import { describe, expect, it } from 'vitest'

import { buildTripOverview, paceLabel, type TripOverviewRow } from './travelOverview'
import type { TravelTripV2 } from '@/api/travelV2'

function makeTrip(overrides?: {
  dayTitles?: Array<string | ''>
  itemCounts?: number[]
  budgetAmount?: number | null
  estimatedCost?: number | null
  lodgingName?: string
}): TravelTripV2 {
  const dayTitles = overrides?.dayTitles ?? ['西湖漫步', '灵隐祈福']
  const itemCounts = overrides?.itemCounts ?? [3, 2]
  return {
    trip_id: 'trip-ov',
    title: '杭州两日',
    status: 'active',
    revision: 4,
    source_template_id: null,
    source_template_version: null,
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-02T00:00:00Z',
    document: {
      schema_version: 2,
      brief: {
        origin: '福州', destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-11-16',
        day_count: dayTitles.length, travelers: { adults: 2, children: 0 },
        budget: { amount: overrides?.budgetAmount === undefined ? 3000 : overrides.budgetAmount, currency: 'CNY' },
        pace: 'relaxed', interests: [], requirements: [],
      },
      selections: [],
      days: dayTitles.map((title, index) => ({
        day_id: `day-${index}`, date: `2026-11-1${index + 6}`, title, legs: [],
        items: Array.from({ length: itemCounts[index] ?? 0 }, (_, itemIndex) => ({
          item_id: `day-${index}-item-${itemIndex}`, kind: 'place', title: `安排${itemIndex}`,
          start_time: '09:00', duration_min: 60, fixed_start: false, must_visit: false,
          locked: false, note: '', place: null,
        })),
      })),
      arrangements: {
        lodgings: overrides?.lodgingName ? [{
          selection_id: 'lodging-1',
          merchant: { merchant_id: 'm-1', name: overrides.lodgingName, address: null, lat: null, lng: null, rating: null, source: 'tencent:lbs', queried_at: '2026-11-01T00:00:00Z' },
          check_in: '2026-11-16', check_out: '2026-11-18', source: 'tencent:lbs',
          queried_at: '2026-11-01T00:00:00Z', verification: 'verified', booking_status: 'not_booked',
          nightly_price_cny: null,
        }] : [],
        intercity_trains: [],
      },
      totals: { currency: 'CNY', estimated_cost: overrides?.estimatedCost === undefined ? 2860 : overrides.estimatedCost, transit_min: 96, distance_m: null },
      health: { status: 'ok', issues: [] },
    },
  } as unknown as TravelTripV2
}

function rowOf(rows: TripOverviewRow[], label: string): string {
  const row = rows.find((entry) => entry.label === label)
  expect(row, `缺少「${label}」行`).toBeDefined()
  return row!.value
}

describe('buildTripOverview（W2 概览统计块取数口径）', () => {
  it('六行标签齐全且顺序与原型 ws-ov 一致', () => {
    const rows = buildTripOverview(makeTrip())
    expect(rows.map((row) => row.label)).toEqual([
      '天数主题', '安排', '预算 / 节奏', '预估花费', '天气', '住宿',
    ])
  })

  it('天数主题 = N 天 · 各天标题（空标题 →（未命名））', () => {
    const rows = buildTripOverview(makeTrip({ dayTitles: ['西湖漫步', ''] }))
    expect(rowOf(rows, '天数主题')).toBe('2 天 · 西湖漫步 + （未命名）')
  })

  it('安排 = 各天条目求和（跨 N 天）', () => {
    const rows = buildTripOverview(makeTrip({ itemCounts: [3, 2] }))
    expect(rowOf(rows, '安排')).toBe('5 个（跨 2 天）')
  })

  it('预算有值显示金额 · 节奏中文化；null → 待设置', () => {
    const rows = buildTripOverview(makeTrip({ budgetAmount: 3000 }))
    expect(rowOf(rows, '预算 / 节奏')).toBe('¥3,000 · 轻松')
    expect(rowOf(buildTripOverview(makeTrip({ budgetAmount: null })), '预算 / 节奏')).toBe('待设置 · 轻松')
  })

  it('预估花费取 totals.estimated_cost 并标注未核验；null → —', () => {
    const rows = buildTripOverview(makeTrip({ estimatedCost: 2860 }))
    expect(rowOf(rows, '预估花费')).toBe('¥2,860（未核验）')
    expect(rowOf(buildTripOverview(makeTrip({ estimatedCost: null })), '预估花费')).toBe('—')
  })

  it('天气为契约位占位（当前无实时预报数据源，不伪造）', () => {
    const rows = buildTripOverview(makeTrip())
    expect(rowOf(rows, '天气')).toBe('出行日临近自动联动预报')
  })

  it('住宿取首条安排名称与入住日；未安排 → 引导语', () => {
    const rows = buildTripOverview(makeTrip({ lodgingName: '杭州湖滨酒店' }))
    expect(rowOf(rows, '住宿')).toBe('杭州湖滨酒店 · 2026-11-16 入住')
    expect(rowOf(buildTripOverview(makeTrip({ lodgingName: undefined })), '住宿'))
      .toBe('未安排 —— 对话或备选确认后选入')
  })
})

describe('paceLabel（从 Workspace 收口到单一事实源）', () => {
  it('三档节奏中文化', () => {
    expect(paceLabel('relaxed')).toBe('轻松')
    expect(paceLabel('intense')).toBe('紧凑')
    expect(paceLabel('balanced')).toBe('均衡')
    expect(paceLabel('unknown-pace')).toBe('均衡')
  })
})
