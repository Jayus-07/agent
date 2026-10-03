'use client'

/**
 * ItineraryView — 行程结果展示（2026-10-01 三栏方案版）
 *
 * 视觉基准 = 用户提供的参考 HTML（旅游规划页面交互示意）：青绿主色
 * #087b73、软底面板 #f5faf9、描边 #dae7e5、墨色文字 #183037、圆角卡片、
 * 按天 Tab（tp-day）、阶段 chips。这些色值**只在本页使用**（页面级主题，
 * 不动全局 design tokens——全局 accent 仍是蓝，改全局要走三端同步纪律）。
 *
 * 结构自上而下：概览（标题+版本徽章+倒计时 / 预算合计+占比条+图例）→
 * 路线地图（**按天分色连线 + 到访序号**，客户端 Web Mercator 投影，与
 * 底图同参数投影保证对齐；连线是「按到访顺序的示意」，不是实际道路——
 * 方案 v2 的不伪造原则）→ 按天 Tab + 单日时间轴 → 出行须知（折叠）→ 文本版。
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import type { Map as LeafletMap } from 'leaflet'
import {
  AlertTriangle, CalendarPlus, Check, Coffee, ListChecks, Route, ThumbsDown, ThumbsUp, TrainFront, UtensilsCrossed,
} from 'lucide-react'
import {
  confirmTravelPlan,
  type Itinerary, type ItineraryDay, type ItineraryItem, type PlanResponse,
  type TransitLeg,
} from '@/api/travel'
import { formatDayDate } from './planState'
import {
  dayLoad, dayMealItems, dayRouteColor, dayVisitTitles, departureBadge, formatDuration,
} from './travelDisplay'
import { classifyFact } from './travelRuntime'

// ── 页面级主题（来源：参考 HTML #trip-concept 的 CSS 变量，勿当全局 token 用） ──
const TP = {
  accent: '#087b73',
  soft: '#f5faf9',
  line: '#dae7e5',
  ink: '#183037',
  muted: '#5c7074',
} as const

// ── 路线图（Leaflet 交互地图：滚轮缩放 / 拖拽平移，fitBounds 按行程自适应） ──

interface DayRoute {
  dayIndex: number
  pts: Array<{ lat: number; lng: number; title: string }>
}

function RouteMap({ itinerary, selectedDay }: { itinerary: Itinerary; selectedDay: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  // 每天的到访点（kind=visit 且有坐标），按当日时间顺序
  // 每天的到访点（kind=visit 且坐标可信），按当日时间顺序。
  // 坐标级判定（字段级拆分 2026-10-02）：location_status=verified（live 检索，
  // 坐标可信）或旧数据回退 source 口径 verified——详情占位（票价/时长
  // verification_status=unverified）只影响展示标注，不再连坐地图打点。
  const dayRoutes = useMemo<DayRoute[]>(() => {
    const routes: DayRoute[] = []
    for (const d of itinerary.days.filter((day) => day.day_index === selectedDay)) {
      const pts = d.items
        .filter((item): item is ItineraryItem & { poi: NonNullable<ItineraryItem['poi']> } =>
          item.kind === 'visit'
            && item.poi != null
            && Number.isFinite(item.poi.lat)
            && Number.isFinite(item.poi.lng)
            && (item.poi.location_status === 'verified'
              || classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status }) === 'verified'))
        .map((item) => ({ lat: item.poi.lat, lng: item.poi.lng, title: item.title }))
      if (pts.length > 0) routes.push({ dayIndex: d.day_index, pts })
    }
    return routes
  }, [itinerary, selectedDay])

  const selectedDayData = itinerary.days.find((day) => day.day_index === selectedDay)
  const missingLocation = Boolean(selectedDayData?.items.some((item) => item.kind === 'visit' && (
    !item.poi
      || !Number.isFinite(item.poi.lat)
      || !Number.isFinite(item.poi.lng)
      || (item.poi.location_status !== 'verified'
        && classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status }) !== 'verified')
  )))

  // 建图（一次）：瓦片走高德（GCJ-02，与种子/静态图坐标同口径），无 Key 前端直连
  useEffect(() => {
    let cancelled = false
    let map: LeafletMap | null = null
    ;(async () => {
      try {
        const L = await import('leaflet')
        if (cancelled || !containerRef.current) return
        map = L.map(containerRef.current, {
          zoomControl: true,
          scrollWheelZoom: true, // 滚轮缩放（用户要求）；容器内滚动互不干扰见 CSS touch-action
          attributionControl: true,
        })
        L.tileLayer(
          'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
          { subdomains: '1234', maxZoom: 18, attribution: '© 高德地图' },
        ).addTo(map)
        map.attributionControl.setPrefix(false)
        leafletRef.current = L
        mapRef.current = map
        setReady(true)
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      map?.remove()
      mapRef.current = null
      layerRef.current = null
    }
  }, [])

  // 画层（行程变化即重画 + 自适应取景）
  useEffect(() => {
    const map = mapRef.current
    const L = leafletRef.current
    if (!ready || !map || !L) return
    layerRef.current?.remove()
    const group = L.layerGroup()
    const bounds: [number, number][] = []
    let seq = 0
    for (const route of dayRoutes) {
      const color = dayRouteColor(route.dayIndex)
      if (route.pts.length >= 2) {
        // 按到访顺序的示意连线（虚线），不是实际道路——诚实口径见 figcaption
        L.polyline(
          route.pts.map((p) => [p.lat, p.lng] as [number, number]),
          { color, weight: 3, dashArray: '8 6', opacity: 0.85 },
        ).addTo(group)
      }
      for (const p of route.pts) {
        seq += 1
        const icon = L.divIcon({
          className: 'travel-seq-marker',
          html: `<span style="display:flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:9999px;background:${color};color:#fff;font:700 11px/16px system-ui;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)">${seq}</span>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        })
        L.marker([p.lat, p.lng], { icon, title: p.title }).addTo(group)
        bounds.push([p.lat, p.lng])
      }
    }
    group.addTo(map)
    layerRef.current = group
    if (bounds.length > 0) {
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 })
    }
  }, [ready, dayRoutes])

  if (failed || dayRoutes.length === 0) {
    return (
      <figure className="animate-fade-in overflow-hidden rounded-2xl border border-red-200 bg-white shadow-card">
        <figcaption className="border-b border-red-100 px-4 py-2 text-xs font-medium text-[#183037]">
          <Route size={13} className="mr-1 inline text-[#087b73]" aria-hidden />第 {selectedDay} 天地图
        </figcaption>
        <p className="px-4 py-8 text-center text-sm text-red-600">
          {failed ? '地图暂时无法加载' : `第 ${selectedDay} 天暂无可用的地点坐标`}
        </p>
      </figure>
    )
  }
  return (
    <figure className="animate-fade-in overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card">
      <figcaption className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[#dae7e5] px-4 py-2 text-xs font-medium text-[#183037]">
        <Route size={13} style={{ color: TP.accent }} aria-hidden />
        第 {selectedDay} 天路线示意
        <span className="font-normal text-[#5c7074]">
          滚轮缩放 · 拖拽平移 · 连线按当天到访顺序，非实际道路
        </span>
        {missingLocation && <span className="ml-auto text-red-600">部分位置暂无核实数据</span>}
      </figcaption>
      {/* 高度放大：一屏布局下中栏独立滚动，地图吃足空间（自适应取景由 fitBounds 保证） */}
      <div ref={containerRef} className="h-[360px] w-full bg-[#f5faf9]" role="img" aria-label="行程路线交互地图" />
    </figure>
  )
}

// ── 逐日时间轴（单日卡片，日卡片条选中后展示） ───────────────

const KIND_STYLE: Record<string, { dot: string; label: string; icon: React.ReactNode }> = {
  visit: { dot: '#087b73', label: '', icon: null },
  meal: { dot: '#d97706', label: '用餐', icon: <UtensilsCrossed size={11} aria-hidden /> },
  rest: { dot: '#059669', label: '休息', icon: <Coffee size={11} aria-hidden /> },
}

/** 入选理由行（2026-10-03）：必去点名 + poi.reason；两者都空则不展示。 */
function reasonLine(item: ItineraryItem): string {
  if (!item.poi) return ''
  const parts = [
    item.poi.required ? '你点名的必去' : '',
    typeof item.poi.reason === 'string' ? item.poi.reason.trim() : '',
  ]
  return parts.filter(Boolean).join(' · ')
}

function ItemTags({ item }: { item: ItineraryItem }) {
  const ticket = item.poi && item.kind === 'visit' ? item.poi.ticket_cny : 0
  const stay = formatDuration(item.minutes ?? 0)
  const factStatus = item.poi
    ? classifyFact({ source: item.poi.source, verification_status: item.poi.verification_status })
    : 'unknown'
  return (
    <span className="ml-1.5 inline-flex flex-wrap items-center gap-1 align-middle">
      {/* 停留时长：排在类型之后的第一个 chip，回答「在这待多久」 */}
      {stay && (
        <span className="rounded bg-[#087b73]/10 px-1.5 py-0.5 text-[10px] text-[#087b73]">
          停留 {stay}
        </span>
      )}
      {item.wait_minutes > 0 && (
        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
          排队 {item.wait_minutes}′
        </span>
      )}
      {ticket > 0 && factStatus === 'verified' && (
        <span className="rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
          门票 ¥{ticket.toFixed(0)} · 已核实
        </span>
      )}
      {item.poi && factStatus !== 'verified' && (
        <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">
          门票暂无数据
        </span>
      )}
    </span>
  )
}

// ── 时间轴交通段（leg 交错进时间轴：两个地点之间「怎么走、多久、多少钱」） ──

/** 后端 TransitLeg.mode 目前只有 walk / drive 两种（models/itinerary.py）；未知值原样展示。 */
const LEG_MODE_LABEL: Record<string, string> = {
  walk: '步行',
  drive: '驾车',
}

/**
 * 时间轴上的交通段行：mode/时长/距离/费用全部来自 day.legs 真实字段。
 * 诚实口径：未核实的路段不显示具体时长与金额（只给 mode + 提示），
 * is_estimate 标「估算」，traffic_aware 标「实时路况」。
 */
function LegRow({ leg }: { leg: TransitLeg }) {
  const fact = classifyFact({ source: leg.source })
  const verified = fact === 'verified'
  const mode = LEG_MODE_LABEL[leg.mode] ?? leg.mode
  const parts: string[] = []
  if (verified) {
    parts.push(formatDuration(leg.minutes))
    if (leg.distance_km > 0) parts.push(`${leg.distance_km.toFixed(1)}km`)
    if (leg.cost_cny > 0) parts.push(`¥${leg.cost_cny.toFixed(0)}`)
  }
  return (
    <div
      className="my-0.5 ml-2 flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg bg-[#f5faf9]/70 px-2 py-1 text-[10px] text-[#8aa0a4]"
      aria-label={`${leg.from_title}到${leg.to_title}的交通`}
    >
      <span aria-hidden className="text-[#c3d6d2]">└─</span>
      <span className="font-medium text-[#5c7074]">{mode}</span>
      {verified
        ? <span>{parts.join(' · ')}</span>
        : <span className="text-red-600">路段数据暂无核实</span>}
      {leg.traffic_aware && verified && <span>实时路况</span>}
      {leg.is_estimate && verified && <span className="text-amber-600">估算值</span>}
    </div>
  )
}

function DayCard({ day }: { day: ItineraryDay }) {
  const load = dayLoad(day)
  // legs[i] 若与 items[i].title 对上 → 交错渲染进时间轴；对不上的（异常/多余）留到底部块，不丢数据。
  const legForItem = new Map<number, TransitLeg>()
  const leftoverLegs: TransitLeg[] = []
  ;(day.legs ?? []).forEach((leg, i) => {
    if (i < day.items.length - 1 && leg.from_title === day.items[i]?.title) {
      legForItem.set(i, leg)
    } else {
      leftoverLegs.push(leg)
    }
  })
  return (
    <section className="animate-fade-in overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card">
      <header className="flex items-center gap-3 border-b border-[#dae7e5] bg-[#f5faf9] px-4 py-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#087b73]/10 text-[11px] font-bold text-[#087b73]">
          D{day.day_index}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-[#183037]">第 {day.day_index} 天</h3>
          <p className="truncate text-[11px] text-[#5c7074]">{formatDayDate(day.day_date)}</p>
        </div>
        <div className="shrink-0 text-right text-[11px] leading-4 text-[#5c7074]">
          <div>活动 {day.active_minutes}′ · 在途 {day.transit_minutes}′</div>
          <div className="font-semibold text-red-600">费用暂无数据</div>
        </div>
      </header>

      {/* 负载条：活动 vs 在途的时间占比（真实派生；空日不画假条） */}
      {load.activeShare + load.transitShare > 0 && (
        <div className="flex h-1 w-full" role="img" aria-label={`活动占 ${Math.round(load.activeShare * 100)}%`}
          title={`活动 ${Math.round(load.activeShare * 100)}% · 在途 ${Math.round(load.transitShare * 100)}%`}>
          <span className="bg-[#087b73]" style={{ width: `${load.activeShare * 100}%` }} />
          <span className="bg-[#f59e0b]" style={{ width: `${load.transitShare * 100}%` }} />
        </div>
      )}

      <ol className="relative ml-6 border-l border-[#dae7e5] py-3 pl-4 pr-4">
        {day.items.map((item, idx) => {
          const style = KIND_STYLE[item.kind] ?? { dot: '#cbd5e1', label: '', icon: null }
          const leg = legForItem.get(idx)
          return (
            <Fragment key={`${item.title}-${idx}`}>
              <li className={`relative rounded-lg px-2 py-1 transition-colors [margin-left:-0.5rem] hover:bg-[#f5faf9] ${idx === day.items.length - 1 ? '' : 'pb-2.5'}`}>
                <span
                  className="absolute -left-[21px] top-2.5 h-2 w-2 rounded-full ring-2 ring-white"
                  style={{ background: style.dot }}
                  aria-hidden
                />
                <div className="flex items-baseline gap-2">
                  <span className="w-[92px] shrink-0 font-mono text-[11px] tabular-nums text-[#5c7074]">
                    {item.start}-{item.end}
                  </span>
                  <span className="min-w-0 flex-1 text-sm text-[#183037]">
                    {item.title}
                    {style.label && (
                      <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
                        {style.icon}
                        {style.label}
                      </span>
                    )}
                    <ItemTags item={item} />
                  </span>
                </div>
                {/* 入选理由（2026-10-03）：「为什么选它」——必去点名/检索来源/知乎攻略提及 */}
                {reasonLine(item) && (
                  <p className="mt-0.5 pl-[100px] text-[11px] leading-relaxed text-[#087b73]">
                    为什么选它：{reasonLine(item)}
                  </p>
                )}
                {item.note && <p className="mt-0.5 pl-[100px] text-[11px] text-[#5c7074]">{item.note}</p>}
              </li>
              {/* 交通段：紧贴在当前地点之后、下一个地点之前，回答「之间怎么走」 */}
              {leg && <LegRow leg={leg} />}
            </Fragment>
          )
        })}
      </ol>

      {leftoverLegs.length > 0 && (
        <div className="border-t border-[#dae7e5] px-4 py-2.5">
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold text-[#5c7074]">
            <Route size={11} className="text-[#087b73]" aria-hidden />
            其他路段信息
          </div>
          <ul className="space-y-1.5">
            {leftoverLegs.map((leg, index) => {
              const fact = classifyFact({ source: leg.source })
              const sourceLabel = fact === 'verified' ? '已核实' : '路线数据暂无核实'
              return (
                <li key={`${leg.from_title}-${leg.to_title}-${index}`} className="flex items-center gap-2 text-[10px] text-[#5c7074]">
                  <span className="min-w-0 flex-1 truncate">{leg.from_title} → {leg.to_title}</span>
                  <span className={`shrink-0 ${fact === 'verified' ? '' : 'text-red-600'}`}>
                    {fact === 'verified' ? formatDuration(leg.minutes) : '暂无数据'} · {sourceLabel}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}

// ── 日卡片条（多天行程的可切换卡片，替代原 chip Tab） ─────────

/**
 * 单张日卡片：D{n} 徽章 + 日期 + 当天亮点（前 2 个真实到访地点）+
 * 地点/用餐计数。亮点只取行程里真实出现的名称（dayVisitTitles），
 * 不生成主题标签——后端契约里没有当天主题字段，不伪造。
 */
function DayTabCard({
  day, selected, onSelect, index = 0,
}: {
  day: ItineraryDay
  selected: boolean
  onSelect: (dayIndex: number) => void
  /** 生成完成后的逐张点亮：按序号错开淡入起点（backwards 保证等待期不可见） */
  index?: number
}) {
  const highlights = dayVisitTitles(day).slice(0, 2)
  const mealCount = dayMealItems(day).length
  const visitCount = dayVisitTitles(day).length
  const load = dayLoad(day)
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      onClick={() => onSelect(day.day_index)}
      className={`group w-[172px] shrink-0 cursor-pointer overflow-hidden rounded-xl border p-3 text-left transition-all animate-fade-in ${
        selected
          ? 'border-[#087b73] bg-[#087b73]/[0.06] shadow-card ring-1 ring-[#087b73]/30'
          : 'border-[#dae7e5] bg-white hover:border-[#087b73]/40 hover:shadow-card'
      }`}
      style={{ animationDelay: `${Math.min(index, 8) * 70}ms`, animationFillMode: 'backwards' }}
    >
      <div className="flex items-center gap-2">
        <span
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold transition-colors ${
            selected ? 'bg-[#087b73] text-white' : 'bg-[#087b73]/10 text-[#087b73]'
          }`}
        >
          D{day.day_index}
        </span>
        <span className={`min-w-0 flex-1 truncate text-[11px] ${selected ? 'font-semibold text-[#087b73]' : 'text-[#5c7074]'}`}>
          {formatDayDate(day.day_date)}
        </span>
        {selected && (
          <span className="shrink-0 rounded-full bg-[#087b73] px-1.5 py-0.5 text-[9px] font-medium text-white">
            查看中
          </span>
        )}
      </div>
      <p className={`mt-2 min-h-[2em] text-xs leading-[1.4] ${selected ? 'font-medium text-[#183037]' : 'text-[#183037]/80'}`}>
        {highlights.length > 0
          ? highlights.join(' · ')
          : <span className="text-[#8fa5a3]">当天暂无到访安排</span>}
      </p>
      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-[#5c7074]">
        <span>{visitCount} 个地点</span>
        {mealCount > 0 && <span className="text-[#d97706]">· {mealCount} 次用餐</span>}
      </div>
      {load.activeShare + load.transitShare > 0 && (
        <div className="mt-1.5 flex h-[3px] w-full overflow-hidden rounded-full bg-[#f5faf9]" aria-hidden>
          <span className="bg-[#087b73]/70" style={{ width: `${load.activeShare * 100}%` }} />
          <span className="bg-[#f59e0b]/70" style={{ width: `${load.transitShare * 100}%` }} />
        </div>
      )}
    </button>
  )
}

// ── 概览条 ───────────────────────────────────────────────────

/**
 * 行程状态机（后端 travel/models/itinerary.py 的 PLAN_STATUS_*）：
 * ready 是常态不挂角标；degraded 表示部分外部数据没取到、行程仍可用。
 */
const STATUS_LABEL: Record<string, string> = {
  ready: '',
  validating: '',
  degraded: '部分数据未取到',
  needs_user_decision: '有需要你拍板的点',
}

/** 费用分类的展示形态（条形颜色 + 图例图标）。顺序 = costBreakdown 的切片顺序。 */
// ── 导出给页面用 ─────────────────────────────────────────────

export interface ItineraryViewProps {
  itinerary: Itinerary
  conversationId: string
  planStatus?: string
  /** 追问 / 失败提示：有行程时也展示，不顶掉行程 */
  notice?: string
  exporting?: boolean
  feedbackSent: '' | 'positive' | 'negative'
  /** 当前选中天（受控）：页面传入后与右侧助手共享「正在看第几天」 */
  selectedDay?: number | null
  onSelectedDayChange?: (dayIndex: number) => void
  onExportIcs: () => void
  onFeedback: (vote: 'positive' | 'negative') => void
  onPlanResponse: (data: PlanResponse) => void
}
export default function ItineraryView({
  itinerary, conversationId, planStatus = '', notice, exporting = false,
  feedbackSent, selectedDay: selectedDayProp, onSelectedDayChange, onExportIcs, onFeedback, onPlanResponse,
}: ItineraryViewProps) {
  const dayCount = itinerary.days.length
  // 受控优先（页面要跟右侧助手共享选中天）；未传时退回内部自管。
  const [innerDay, setInnerDay] = useState(1)
  const selectedDay = selectedDayProp ?? innerDay
  const setSelectedDay = (dayIndex: number) => {
    if (onSelectedDayChange) onSelectedDayChange(dayIndex)
    else setInnerDay(dayIndex)
  }
  // 选中天默认取返回结果的第一天；后端历史数据的 day_index 不一定从 1 开始。
  const activeDayData = itinerary.days.find((d) => d.day_index === selectedDay) ?? itinerary.days[0]
  const activeDay = activeDayData?.day_index ?? 1
  // 当日亮点 chips：全部从真实字段派生——必去=前 2 个到访地点、美食=当天用餐、
  // 提示=当天存在估算路段。后端没有「当天主题」字段，不伪造标签。
  const dayHighlights = useMemo(() => {
    if (!activeDayData) return [] as Array<{ text: string; tone: 'spot' | 'meal' | 'tip' }>
    const chips: Array<{ text: string; tone: 'spot' | 'meal' | 'tip' }> = []
    dayVisitTitles(activeDayData).slice(0, 2).forEach((title) => chips.push({ text: `必去 ${title}`, tone: 'spot' }))
    dayMealItems(activeDayData).slice(0, 2).forEach((meal) => chips.push({ text: meal.title, tone: 'meal' }))
    if ((activeDayData.legs ?? []).some((leg) => leg.is_estimate)) {
      chips.push({ text: '部分路段为估算值', tone: 'tip' })
    }
    return chips
  }, [activeDayData])
  // 当天出发：取第一趟城际班次 + 第一个有价的席别（真实字段，无则整段不显示）
  const intercity = itinerary.intercity && itinerary.intercity.length > 0 ? itinerary.intercity[0] : null
  const intercitySeat = intercity ? Object.entries(intercity.prices ?? {})[0] : null
  const badge = departureBadge(itinerary.brief.start_date)
  useEffect(() => {
    if (itinerary.days.length > 0 && !itinerary.days.some((day) => day.day_index === selectedDay)) {
      setSelectedDay(itinerary.days[0].day_index)
    }
  }, [itinerary.days, selectedDay])
  const [actionLoading, setActionLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [localPlanStatus, setLocalPlanStatus] = useState(planStatus)

  useEffect(() => setLocalPlanStatus(planStatus), [planStatus])

  const confirmCurrent = async () => {
    setActionLoading(true)
    setHistoryError('')
    try {
      await confirmTravelPlan(conversationId, itinerary.plan_version)
      setLocalPlanStatus('confirmed')
      onPlanResponse({
        status: 'ready', final_answer: '', itinerary, plan_status: 'confirmed',
      })
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : '确认失败，请刷新后重试')
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {notice && (
        <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
          <p className="min-w-0 flex-1 whitespace-pre-wrap">{notice}</p>
        </div>
      )}

      {/* 标题行（设计稿③）：行程名 + 版本徽章 | 确认 / 导出 / 反馈 + 距出发 */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="text-2xl font-bold text-[#183037]">
          {itinerary.brief.destination || '未命名目的地'} · {dayCount} 天
        </h2>
        <span className="rounded-full bg-[#e2f0ee] px-2.5 py-1 text-xs font-medium text-[#087b73]">
          v{itinerary.plan_version} {localPlanStatus === 'waiting_confirmation' ? '草案' : '已确认'}
        </span>
        {STATUS_LABEL[itinerary.status] && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
            {STATUS_LABEL[itinerary.status]}
          </span>
        )}
        {localPlanStatus === 'waiting_confirmation' && (
          <button
            type="button"
            onClick={() => void confirmCurrent()}
            disabled={actionLoading}
            className="inline-flex items-center gap-1 rounded-lg bg-[#087b73] px-2.5 py-1.5 text-[11px] text-white hover:bg-[#06655f] disabled:opacity-50"
          >
            <Check size={12} /> {actionLoading ? '确认中…' : '确认行程'}
          </button>
        )}
        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={onExportIcs}
              disabled={exporting}
              title={exporting ? '导出中…' : '导出日历'}
              aria-label="导出日历"
              className="rounded-lg border border-[#dae7e5] p-1.5 text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037] disabled:opacity-50"
            >
              <CalendarPlus size={14} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => onFeedback('positive')}
              disabled={!!feedbackSent}
              title="这份行程有用"
              aria-label="这份行程有用"
              className={`rounded-lg border p-1.5 transition-colors ${
                feedbackSent === 'positive'
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                  : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
              }`}
            >
              <ThumbsUp size={14} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => onFeedback('negative')}
              disabled={!!feedbackSent}
              title="需要改"
              aria-label="需要改"
              className={`rounded-lg border p-1.5 transition-colors ${
                feedbackSent === 'negative'
                  ? 'border-red-300 bg-red-50 text-red-700'
                  : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
              }`}
            >
              <ThumbsDown size={14} aria-hidden />
            </button>
          </div>
          {badge && (
            <span
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium text-white"
              style={{ background: TP.accent }}
            >
              <CalendarPlus size={11} aria-hidden />
              {badge}
            </span>
          )}
        </div>
      </header>
      {historyError && <p className="text-xs text-red-600">{historyError}</p>}

      {/* 设计稿③：日卡片条（横向滚动、选中描边）+ 一张大卡（当日亮点 / 当天出发 / 时间轴|地图双列） */}
      {dayCount > 1 && (
        <div
          role="tablist"
          aria-label="行程日期"
          className="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:thin]"
        >
          {itinerary.days.map((d, index) => (
            <DayTabCard
              key={d.day_index}
              day={d}
              selected={d.day_index === activeDay}
              onSelect={setSelectedDay}
              index={index}
            />
          ))}
        </div>
      )}
      {activeDayData && (
        <section
          className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card sm:p-5"
          aria-label={`第 ${activeDay} 天行程`}
        >
          {/* 当日亮点：选中天的第一眼摘要（地点/美食/提示），随日卡片切换联动 */}
          {dayHighlights.length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-1.5" aria-label={`第 ${activeDay} 天亮点`}>
              <span className="text-xs font-medium text-[#5c7074]">当日亮点</span>
              {dayHighlights.map((chip) => (
                <span
                  key={chip.text}
                  className={`animate-fade-in rounded-full px-2 py-0.5 text-[11px] ${
                    chip.tone === 'spot'
                      ? 'bg-[#e2f0ee] text-[#087b73]'
                      : chip.tone === 'meal'
                        ? 'bg-[#fdf1e0] text-[#b4690e]'
                        : 'bg-[#f5faf9] text-[#5c7074]'
                  }`}
                >
                  {chip.text}
                </span>
              ))}
            </div>
          )}

          {/* 当天出发：城际班次（12306 实时检索，有数据才显示） */}
          {intercity && (
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-[#f5faf9] px-3.5 py-2.5">
              <TrainFront size={14} style={{ color: TP.accent }} aria-hidden />
              <span className="text-xs font-semibold text-[#183037]">当天出发</span>
              <span className="text-xs text-[#183037]">
                <span className="font-semibold">{intercity.train_no}</span>
                {' '}{intercity.from_station} {intercity.start_time || '--'} → {intercity.to_station} {intercity.arrive_time || '--'}
                {intercitySeat && (
                  <span className="ml-1">
                    · {intercitySeat[0]} {/^\d+(\.\d+)?$/.test(String(intercitySeat[1])) ? `¥${intercitySeat[1]}` : intercitySeat[1]}
                  </span>
                )}
              </span>
              <span className="rounded-full bg-[#e2f0ee] px-2 py-0.5 text-[10px] text-[#087b73]">12306 已核实</span>
              <span className="w-full text-[10px] text-[#8c7258]">
                余票与票价来自 12306 非官方聚合源，可能延迟；购票请以 12306 官方为准。
              </span>
            </div>
          )}

          {/* 双列：时间轴 | 路线地图 */}
          <div className="grid items-start gap-3 xl:grid-cols-2">
            <DayCard day={activeDayData} />
            <RouteMap itinerary={itinerary} selectedDay={activeDay} />
          </div>
        </section>
      )}

      {itinerary.warnings.length > 0 && (
        // 出行须知默认折叠：内容全是「数据源未接入」的兜底说明，逐条平铺
        // 会把逐日行程挤下屏；degraded 的行程级警示不在这里，已挂标题行角标。
        <details className="group animate-fade-in rounded-2xl border border-[#dae7e5] bg-white shadow-card">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-3 text-sm font-semibold text-[#5c7074] transition-colors hover:text-[#183037] [&::-webkit-details-marker]:hidden">
            <ListChecks size={14} className="text-amber-600" aria-hidden />
            出行须知
            <span className="text-[11px] font-normal text-[#5c7074]">{itinerary.warnings.length} 条</span>
            <span
              className="ml-auto text-[#5c7074] transition-transform duration-200 group-open:rotate-180"
              aria-hidden
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
            </span>
          </summary>
          <ul className="list-inside list-disc space-y-1 border-t border-[#dae7e5] px-4 py-3 text-sm text-[#5c7074]">
            {itinerary.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}
