/**
 * travelDisplay 回归测试 — 行程展示派生规则（纯函数直测，同 planState.test.ts 惯例）
 *
 * 锁的是展示语义的坑：
 * - 倒计时跨 UTC/本地时区差一天（`T00:00:00` 本地解析口径）
 * - 合计为 0 时不产生假占比（条形宽度直接由 share 驱动，0 share 必须 = 0）
 * - 城市色相确定性（同城市同色，重渲染不闪烁）
 */
import { describe, it, expect } from 'vitest'
import {
  cityHue,
  costBreakdown,
  dayLoad,
  dayMealItems,
  dayRouteColor,
  dayVisitTitles,
  daysUntil,
  departureBadge,
  formatDuration,
  stayLabel,
  TRAVEL_STAGE_LABELS,
  TRAVEL_STAGE_ORDER,
  TRAVEL_TOOL_LABELS,
} from './travelDisplay'
import type { ItineraryDay, ItineraryItem } from '@/api/travel'

/** 最小行程条目 fixture（只填派生函数消费的字段） */
function item(partial: Partial<ItineraryItem> & { kind: string }): ItineraryItem {
  return {
    title: partial.title ?? '',
    kind: partial.kind,
    start: partial.start ?? '09:00',
    end: partial.end ?? '10:00',
    minutes: partial.minutes ?? 60,
    wait_minutes: partial.wait_minutes ?? 0,
    note: partial.note ?? '',
    poi: partial.poi ?? null,
  }
}

function day(items: ItineraryItem[]): ItineraryDay {
  return {
    day_index: 1,
    day_date: null,
    items,
    active_minutes: 0,
    transit_minutes: 0,
    cost_cny: 0,
  }
}

describe('formatDuration', () => {
  it('不足 60 分钟保留分钟口径', () => {
    expect(formatDuration(45)).toBe('45′')
    expect(formatDuration(60 - 1)).toBe('59′')
  })
  it('整小时不带小数点', () => {
    expect(formatDuration(120)).toBe('2h')
  })
  it('非整小时保留一位小数', () => {
    expect(formatDuration(90)).toBe('1.5h')
  })
  it('0/负数/NaN 返回空串（UI 不渲染空 chip）', () => {
    expect(formatDuration(0)).toBe('')
    expect(formatDuration(-5)).toBe('')
    expect(formatDuration(Number.NaN)).toBe('')
  })
})

describe('daysUntil / departureBadge', () => {
  it('未来日期按自然日差计算', () => {
    const target = new Date()
    target.setDate(target.getDate() + 3)
    const iso = target.toISOString().slice(0, 10)
    // toISOString 是 UTC——跨时区可能差一天，构造本地 ISO 更稳
    const localIso = `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, '0')}-${String(target.getDate()).padStart(2, '0')}`
    expect(daysUntil(localIso)).toBe(3)
    expect(departureBadge(localIso)).toBe('距出发 3 天')
    void iso
  })
  it('今天出发 → 0 天与「今天出发」', () => {
    const now = new Date()
    const localIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    expect(daysUntil(localIso)).toBe(0)
    expect(departureBadge(localIso)).toBe('今天出发')
  })
  it('过去的日期不出现负数文案', () => {
    expect(departureBadge('2000-01-01')).toBe('今天出发')
  })
  it('无日期/非法日期返回 null', () => {
    expect(daysUntil(null)).toBeNull()
    expect(daysUntil('')).toBeNull()
    expect(daysUntil('not-a-date')).toBeNull()
    expect(departureBadge(null)).toBeNull()
  })
})

describe('costBreakdown', () => {
  it('按合计派生各分类占比', () => {
    const { slices, total } = costBreakdown({ tickets: 100, meals: 200, lodging: 300, transit: 400 })
    expect(total).toBe(1000)
    expect(slices.map((s) => s.share)).toEqual([0.1, 0.2, 0.3, 0.4])
    expect(slices.map((s) => s.label)).toEqual(['门票', '餐饮', '住宿', '通勤'])
  })
  it('合计为 0 时占比全 0（不产生 NaN）', () => {
    const { slices, total } = costBreakdown({ tickets: 0, meals: 0, lodging: 0, transit: 0 })
    expect(total).toBe(0)
    expect(slices.every((s) => s.share === 0)).toBe(true)
  })
  it('cost 缺省字段按 0 兜底', () => {
    const { total } = costBreakdown(undefined)
    expect(total).toBe(0)
  })
})

describe('dayLoad', () => {
  it('活动/在途归一化到 0-1', () => {
    expect(dayLoad({ active_minutes: 300, transit_minutes: 100 })).toEqual({
      activeShare: 0.75, transitShare: 0.25,
    })
  })
  it('空日全 0，UI 隐藏条形', () => {
    expect(dayLoad({ active_minutes: 0, transit_minutes: 0 })).toEqual({
      activeShare: 0, transitShare: 0,
    })
  })
})

describe('cityHue / stayLabel', () => {
  it('同一城市色相确定，不同城市大概率不同', () => {
    expect(cityHue('厦门')).toBe(cityHue('厦门'))
    expect(cityHue('厦门')).not.toBe(cityHue('杭州'))
  })
  it('色相落在 0-359', () => {
    const hue = cityHue('乌鲁木齐')
    expect(hue).toBeGreaterThanOrEqual(0)
    expect(hue).toBeLessThan(360)
  })
  it('stayLabel 直接映射 formatDuration', () => {
    expect(stayLabel({ minutes: 300 })).toBe('5h')
    expect(stayLabel({ minutes: 0 })).toBe('')
  })
})


describe('dayRouteColor', () => {
  it('按天取色且循环（首位 = 页面主题青绿）', () => {
    expect(dayRouteColor(1)).toBe('#087b73')
    expect(dayRouteColor(2)).toBe('#d97706')
    expect(dayRouteColor(7)).toBe('#087b73')
  })
})

describe('dayVisitTitles / dayMealItems（日卡片亮点与当日分区共用）', () => {
  it('只取到访地点，按时间轴顺序', () => {
    const d = day([
      item({ kind: 'visit', title: '鼓浪屿' }),
      item({ kind: 'meal', title: '午餐' }),
      item({ kind: 'visit', title: '南普陀寺' }),
    ])
    expect(dayVisitTitles(d)).toEqual(['鼓浪屿', '南普陀寺'])
    expect(dayVisitTitles(d)).toHaveLength(2)
  })
  it('空 title 的条目不进亮点（不渲染空文案）', () => {
    const d = day([item({ kind: 'visit', title: '' }), item({ kind: 'visit', title: '三坊七巷' })])
    expect(dayVisitTitles(d)).toEqual(['三坊七巷'])
  })
  it('用餐项单独取，与地点互不混入', () => {
    const d = day([
      item({ kind: 'visit', title: '鼓浪屿' }),
      item({ kind: 'meal', title: '午餐' }),
      item({ kind: 'rest', title: '休息' }),
    ])
    expect(dayMealItems(d).map((m) => m.title)).toEqual(['午餐'])
  })
  it('空日返回空数组（UI 隐藏分区而不是报错）', () => {
    const d = day([])
    expect(dayVisitTitles(d)).toEqual([])
    expect(dayMealItems(d)).toEqual([])
  })
})

describe('TRAVEL_STAGE_ORDER / TRAVEL_STAGE_LABELS / TRAVEL_TOOL_LABELS（过程面板单一源）', () => {
  it('阶段流水线覆盖全部 10 个域图节点且各有中文名', () => {
    expect(TRAVEL_STAGE_ORDER).toHaveLength(10)
    for (const key of TRAVEL_STAGE_ORDER) {
      expect(TRAVEL_STAGE_LABELS[key], `缺 ${key} 的中文名`).toBeTruthy()
    }
  })
  it('slot_filler 在 reporter 之前（stepper 顺序 = 域图拓扑）', () => {
    expect(TRAVEL_STAGE_ORDER[0]).toBe('travel_slot_filler')
    expect(TRAVEL_STAGE_ORDER[TRAVEL_STAGE_ORDER.length - 1]).toBe('travel_reporter')
  })
  it('真实 Tool 键有业务名映射', () => {
    expect(TRAVEL_TOOL_LABELS['travel.search_poi']).toBeTruthy()
    expect(TRAVEL_TOOL_LABELS['map_merchant_search_tool']).toBeTruthy()
    expect(TRAVEL_TOOL_LABELS['travel_train_search_tool']).toBeTruthy()
  })
})
