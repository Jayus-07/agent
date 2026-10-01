/**
 * components/travel/travelDisplay.ts — 行程展示的派生规则（纯函数，直测）
 *
 * 为什么单独抽出来：重做展示后，页面上大量「智能感」元素（出发倒计时、
 * 费用占比条、逐日负载条、停留时长、灵感卡配色、地图路线投影）都是**从既有
 * 契约字段派生**的展示规则，不是新数据。放这里与组件解耦，可像 planState.ts
 * 一样直测；也守住 G2 的精神——派生量只有这一处实现，页面与结果视图共用。
 */
import type { ItineraryCost, ItineraryDay, ItineraryItem } from '@/api/travel'

// ── 时长 ─────────────────────────────────────────────────────

/**
 * 分钟 → 人读时长。≥60 分钟折「N.Nh」（半小时粒度一位小数足够读），
 * 不足 60 分钟保留「N′」——与排队/在途既有口径一致。
 */
export function formatDuration(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return ''
  if (minutes < 60) return `${Math.round(minutes)}′`
  const h = minutes / 60
  return `${Number.isInteger(h) ? h : h.toFixed(1)}h`
}

// ── 出发倒计时 ───────────────────────────────────────────────

/**
 * 出发日期 → 距今天数（自然日差）。无日期返回 null。
 * 用 `T00:00:00` 本地时区解析（与 formatDayDate 同一口径，避免 UTC 偏移差一天）。
 */
export function daysUntil(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null
  const target = new Date(`${dateStr}T00:00:00`)
  if (Number.isNaN(target.getTime())) return null
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - today.getTime()) / 86_400_000)
}

/** 倒计时 → 展示文案（负数 = 日期已过，按「已出发」处理，不出现负数文案）。 */
export function departureBadge(dateStr: string | null | undefined): string | null {
  const d = daysUntil(dateStr)
  if (d === null) return null
  if (d <= 0) return '今天出发'
  return `${d} 天后出发`
}

// ── 费用占比 ─────────────────────────────────────────────────

export interface CostSlice {
  key: 'tickets' | 'meals' | 'lodging' | 'transit'
  label: string
  value: number
  /** 占合计的 0-1；合计为 0 时为 0（条形宽度直接乘 100%） */
  share: number
}

const COST_ORDER: Array<{ key: CostSlice['key']; label: string }> = [
  { key: 'tickets', label: '门票' },
  { key: 'meals', label: '餐饮' },
  { key: 'lodging', label: '住宿' },
  { key: 'transit', label: '通勤' },
]

/**
 * 费用 → 占比切片（展示顺序固定：门票/餐饮/住宿/通勤，与概览数字格一致）。
 * 合计为 0（尚未出费用）时不硬凑百分比，全 0 由 UI 隐藏条形。
 */
export function costBreakdown(cost: ItineraryCost | undefined): { slices: CostSlice[]; total: number } {
  const total =
    cost?.total ??
    (cost ? (cost.tickets ?? 0) + (cost.meals ?? 0) + (cost.lodging ?? 0) + (cost.transit ?? 0) : 0)
  const slices = COST_ORDER.map(({ key, label }) => ({
    key,
    label,
    value: cost?.[key] ?? 0,
    share: total > 0 ? (cost?.[key] ?? 0) / total : 0,
  }))
  return { slices, total }
}

// ── 逐日负载 ─────────────────────────────────────────────────

/**
 * 单日 活动/在途 分钟占比（头部 micro bar 用）。
 * 两者全 0（空日）时 share 全 0，UI 隐藏条形而不是画一条假满条。
 */
export function dayLoad(day: Pick<ItineraryDay, 'active_minutes' | 'transit_minutes'>): {
  activeShare: number
  transitShare: number
} {
  const total = (day.active_minutes ?? 0) + (day.transit_minutes ?? 0)
  if (total <= 0) return { activeShare: 0, transitShare: 0 }
  return {
    activeShare: (day.active_minutes ?? 0) / total,
    transitShare: (day.transit_minutes ?? 0) / total,
  }
}

// ── 灵感卡配色 ───────────────────────────────────────────────

/**
 * 城市名 → 确定性色相（0-359）。灵感卡渐变背景用。
 * 为什么不用随机：同一城市每次渲染同色，卡片不闪烁、快照稳定；
 * FNV-1a 足够散开中文城市名的码点差异。
 */
export function cityHue(city: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < city.length; i++) {
    hash ^= city.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return ((hash >>> 0) % 360)
}

// ── 条目展示 ─────────────────────────────────────────────────

/**
 * 单条时间轴条目的停留时长文案。用餐/休息没有 minutes 语义差异，
 * 统一按 minutes 派生；0/缺省返回空串（UI 不渲染空 chip）。
 */
export function stayLabel(item: Pick<ItineraryItem, 'minutes'>): string {
  return formatDuration(item.minutes ?? 0)
}

// ── 地图路线投影（静态图 + 按天连线） ────────────────────────

export interface LatLng {
  lat: number
  lng: number
}

/** Web Mercator 基础瓦片尺寸（腾讯/高德/OSM 同源投影）。 */
const TILE_SIZE = 256

function mercatorX(lng: number, scale: number): number {
  return ((lng + 180) / 360) * scale
}

function mercatorY(lat: number, scale: number): number {
  const rad = (Math.max(-85.05112878, Math.min(85.05112878, lat)) * Math.PI) / 180
  // tan(π/4 + φ/2) ≡ tanφ + secφ（slippy-map 标准纵轴）
  const y = Math.log(Math.tan(Math.PI / 4 + rad / 2))
  return (0.5 - y / (2 * Math.PI)) * scale
}

/**
 * 经纬度 → 静态图上的像素坐标。
 *
 * 前提：静态图请求必须带**显式 center + zoom + size**（不传 markers），
 * 服务端原样转发给地图服务商，投影结果才与底图严格对齐——markers 让
 * 服务端自动取景时视窗不确定，客户端算不出像素位置。
 */
export function latLngToPixel(
  point: LatLng,
  center: LatLng,
  zoom: number,
  width: number,
  height: number,
): { x: number; y: number } {
  const scale = TILE_SIZE * 2 ** zoom
  return {
    x: mercatorX(point.lng, scale) - mercatorX(center.lng, scale) + width / 2,
    y: mercatorY(point.lat, scale) - mercatorY(center.lat, scale) + height / 2,
  }
}

/** 多点几何中心（简单均值即可——静态图取景不是测绘用途）。 */
export function pointsCentroid(points: LatLng[]): LatLng {
  if (points.length === 0) return { lat: 0, lng: 0 }
  const sum = points.reduce((acc, p) => ({ lat: acc.lat + p.lat, lng: acc.lng + p.lng }), { lat: 0, lng: 0 })
  return { lat: sum.lat / points.length, lng: sum.lng / points.length }
}

/**
 * 让全部落点带边距地装进视窗的最大整数 zoom。
 * 找不到（点过散）时返回 minZoom——静态图仍会出图，连线允许贴边。
 */
export function fitZoom(
  points: LatLng[],
  center: LatLng,
  width: number,
  height: number,
  { padding = 44, minZoom = 8, maxZoom = 15 }: { padding?: number; minZoom?: number; maxZoom?: number } = {},
): number {
  for (let zoom = maxZoom; zoom > minZoom; zoom--) {
    const fits = points.every((p) => {
      const { x, y } = latLngToPixel(p, center, zoom, width, height)
      return x >= padding && x <= width - padding && y >= padding && y <= height - padding
    })
    if (fits) return zoom
  }
  return minZoom
}

/**
 * 按天路线色板（图例与连线共用同一份）。
 * 首位 = 页面主题青绿（参考 HTML 的 --tp-accent），后续按天轮换。
 */
export const DAY_ROUTE_COLORS = ['#087b73', '#d97706', '#7c3aed', '#2563eb', '#db2777', '#0d9488'] as const

export function dayRouteColor(dayIndex: number): string {
  return DAY_ROUTE_COLORS[(dayIndex - 1) % DAY_ROUTE_COLORS.length]
}
