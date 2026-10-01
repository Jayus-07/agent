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
  dayRouteColor,
  daysUntil,
  departureBadge,
  fitZoom,
  formatDuration,
  latLngToPixel,
  pointsCentroid,
  stayLabel,
} from './travelDisplay'

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
    expect(departureBadge(localIso)).toBe('3 天后出发')
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

describe('latLngToPixel / fitZoom', () => {
  it('中心点落在视窗正中（Web Mercator 标准）', () => {
    const p = latLngToPixel({ lat: 24.48, lng: 118.08 }, { lat: 24.48, lng: 118.08 }, 12, 640, 360)
    expect(p.x).toBeCloseTo(320, 5)
    expect(p.y).toBeCloseTo(180, 5)
  })
  it('北边的点 y 更小、东边的点 x 更大', () => {
    const center = { lat: 24.48, lng: 118.08 }
    const north = latLngToPixel({ lat: 24.6, lng: 118.08 }, center, 12, 640, 360)
    const east = latLngToPixel({ lat: 24.48, lng: 118.3 }, center, 12, 640, 360)
    expect(north.y).toBeLessThan(180)
    expect(east.x).toBeGreaterThan(320)
  })
  it('zoom 越大偏移像素越多（同一经差）', () => {
    const center = { lat: 24.48, lng: 118.08 }
    const z10 = latLngToPixel({ lat: 24.48, lng: 118.3 }, center, 10, 640, 360)
    const z13 = latLngToPixel({ lat: 24.48, lng: 118.3 }, center, 13, 640, 360)
    expect(Math.abs(z13.x - 320)).toBeGreaterThan(Math.abs(z10.x - 320))
  })
  it('fitZoom：聚集的点给高 zoom，散点降到低 zoom', () => {
    const center = { lat: 24.48, lng: 118.1 }
    const tight = [
      { lat: 24.47, lng: 118.07 }, { lat: 24.49, lng: 118.12 },
    ]
    const wide = [
      { lat: 24.2, lng: 117.6 }, { lat: 24.9, lng: 118.8 },
    ]
    expect(fitZoom(tight, center, 640, 360)).toBeGreaterThan(fitZoom(wide, center, 640, 360))
  })
  it('fitZoom：单点取 maxZoom', () => {
    expect(fitZoom([{ lat: 24.48, lng: 118.08 }], { lat: 24.48, lng: 118.08 }, 640, 360)).toBe(15)
  })
  it('dayRouteColor 按天取色且循环', () => {
    expect(dayRouteColor(1)).toBe('#087b73')
    expect(dayRouteColor(2)).toBe('#d97706')
    expect(dayRouteColor(7)).toBe('#087b73')
  })
  it('pointsCentroid 均值中心', () => {
    const c = pointsCentroid([{ lat: 10, lng: 100 }, { lat: 20, lng: 110 }])
    expect(c).toEqual({ lat: 15, lng: 105 })
  })
})
