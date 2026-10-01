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
import { useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import type { Map as LeafletMap } from 'leaflet'
import {
  AlertTriangle, BedDouble, CalendarPlus, Coffee, ListChecks, Route, ThumbsDown, ThumbsUp,
  TrainFront, Ticket, UtensilsCrossed, Wallet,
} from 'lucide-react'
import type { Itinerary, ItineraryDay, ItineraryItem } from '@/api/travel'
import { formatDayDate, itineraryTotal, PACE_LABEL } from './planState'
import {
  costBreakdown, dayLoad, dayRouteColor, departureBadge, formatDuration,
} from './travelDisplay'
import MarkdownContent from '@/components/chat/MarkdownContent'

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

function RouteMap({ itinerary }: { itinerary: Itinerary }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  // 每天的到访点（kind=visit 且有坐标），按当日时间顺序
  const dayRoutes = useMemo<DayRoute[]>(() => {
    const routes: DayRoute[] = []
    for (const d of itinerary.days) {
      const pts = d.items
        .filter((item): item is ItineraryItem & { poi: NonNullable<ItineraryItem['poi']> } =>
          item.kind === 'visit' && item.poi != null)
        .map((item) => ({ lat: item.poi.lat, lng: item.poi.lng, title: item.title }))
      if (pts.length > 0) routes.push({ dayIndex: d.day_index, pts })
    }
    return routes
  }, [itinerary])

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

  if (dayRoutes.length === 0 || failed) return null
  return (
    <figure className="animate-fade-in overflow-hidden rounded-2xl border border-[#dae7e5] bg-white shadow-card">
      <figcaption className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[#dae7e5] px-4 py-2 text-xs font-medium text-[#183037]">
        <Route size={13} style={{ color: TP.accent }} aria-hidden />
        路线示意
        <span className="font-normal text-[#5c7074]">
          滚轮缩放 · 拖拽平移 · 连线按当天到访顺序，非实际道路
        </span>
        {/* 按天图例：与连线同色 */}
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {dayRoutes.map((d) => (
            <span
              key={d.dayIndex}
              className="inline-flex items-center gap-1 rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[10px] text-[#183037]"
            >
              <span className="h-2 w-2 rounded-full" style={{ background: dayRouteColor(d.dayIndex) }} aria-hidden />
              第 {d.dayIndex} 天 · {d.pts.length} 站
            </span>
          ))}
        </span>
      </figcaption>
      {/* 高度放大：一屏布局下中栏独立滚动，地图吃足空间（自适应取景由 fitBounds 保证） */}
      <div ref={containerRef} className="h-[360px] w-full bg-[#f5faf9]" role="img" aria-label="行程路线交互地图" />
    </figure>
  )
}

// ── 逐日时间轴（单日卡片，Tab 选中后展示） ───────────────────

const KIND_STYLE: Record<string, { dot: string; label: string; icon: React.ReactNode }> = {
  visit: { dot: '#087b73', label: '', icon: null },
  meal: { dot: '#d97706', label: '用餐', icon: <UtensilsCrossed size={11} aria-hidden /> },
  rest: { dot: '#059669', label: '休息', icon: <Coffee size={11} aria-hidden /> },
}

function ItemTags({ item }: { item: ItineraryItem }) {
  const ticket = item.poi && item.kind === 'visit' ? item.poi.ticket_cny : 0
  const stay = formatDuration(item.minutes ?? 0)
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
      {ticket > 0 && (
        <span className="rounded bg-[#f5faf9] px-1.5 py-0.5 text-[10px] text-[#5c7074]">
          门票 ¥{ticket.toFixed(0)}
        </span>
      )}
    </span>
  )
}

function DayCard({ day }: { day: ItineraryDay }) {
  const load = dayLoad(day)
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
          <div className="font-semibold text-[#183037]">¥{day.cost_cny.toFixed(0)}</div>
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
          return (
            <li key={idx} className={`relative rounded-lg px-2 py-1 transition-colors [margin-left:-0.5rem] hover:bg-[#f5faf9] ${idx === day.items.length - 1 ? '' : 'pb-2.5'}`}>
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
              {item.note && <p className="mt-0.5 pl-[100px] text-[11px] text-[#5c7074]">{item.note}</p>}
            </li>
          )
        })}
      </ol>
    </section>
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
const COST_META: Record<string, { bar: string; icon: React.ReactNode }> = {
  tickets: { bar: '#087b73', icon: <Ticket size={12} aria-hidden /> },
  meals: { bar: '#d97706', icon: <UtensilsCrossed size={12} aria-hidden /> },
  lodging: { bar: '#0d9488', icon: <BedDouble size={12} aria-hidden /> },
  transit: { bar: '#94a3b8', icon: <TrainFront size={12} aria-hidden /> },
}

function OverviewBar({
  itinerary, exporting, feedbackSent, onExportIcs, onFeedback,
}: {
  itinerary: Itinerary
  exporting: boolean
  feedbackSent: '' | 'positive' | 'negative'
  onExportIcs: () => void
  onFeedback: (vote: 'positive' | 'negative') => void
}) {
  const { brief, cost } = itinerary
  const { slices, total } = costBreakdown(cost)
  const perPerson = brief.party_size > 0 ? total / brief.party_size : total

  // 需求摘要 chips：右侧「旅行助手」要改的就是这几项，看不见就没法改。
  const meta: string[] = []
  if (brief.party_size) meta.push(`${brief.party_size} 人`)
  meta.push(`节奏${PACE_LABEL[brief.pace] ?? '适中'}`)
  if (brief.preferences.length) meta.push(`偏好 ${brief.preferences.join('、')}`)
  if (brief.must_go.length) meta.push(`必去 ${brief.must_go.join('、')}`)
  if (brief.avoid?.length) meta.push(`避开 ${brief.avoid.join('、')}`)
  // 忌口必须露出来：它是「不能出错」的约束，藏起来用户根本不知道系统记住了
  if (brief.diet) meta.push(`忌口 ${brief.diet}`)

  const badge = departureBadge(brief.start_date)

  return (
    <section className="animate-fade-in rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-[#183037]">
              {brief.destination || '未命名目的地'} · {itinerary.days.length} 天
            </h2>
            <span className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#183037]">
              v{itinerary.plan_version}
            </span>
            {/* 出发倒计时：真实派生；无日期不渲染（「未定日期」在逐日卡里已有） */}
            {badge && (
              <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium text-white"
                style={{ background: TP.accent }}>
                <CalendarPlus size={11} aria-hidden />
                {badge}
              </span>
            )}
            {STATUS_LABEL[itinerary.status] && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
                {STATUS_LABEL[itinerary.status]}
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {brief.start_date && (
              <span className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#5c7074]">
                {brief.start_date} 出发
              </span>
            )}
            {meta.map((m) => (
              <span
                key={m}
                className="rounded-full border border-[#dae7e5] bg-[#f5faf9] px-2 py-0.5 text-[11px] text-[#5c7074]"
              >
                {m}
              </span>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={onExportIcs}
            disabled={exporting}
            className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-[#dae7e5] px-2.5 py-1.5
              text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]
              disabled:cursor-not-allowed disabled:opacity-50"
          >
            <CalendarPlus size={13} aria-hidden />
            {exporting ? '导出中…' : '导出日历'}
          </button>
          <button
            type="button"
            onClick={() => onFeedback('positive')}
            disabled={!!feedbackSent}
            title="这份行程有用"
            aria-label="这份行程有用"
            className={`flex cursor-pointer items-center rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              feedbackSent === 'positive'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
            }`}
          >
            <ThumbsUp size={13} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onFeedback('negative')}
            disabled={!!feedbackSent}
            title="需要改"
            aria-label="需要改"
            className={`flex cursor-pointer items-center rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              feedbackSent === 'negative'
                ? 'border-red-300 bg-red-50 text-red-700'
                : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
            }`}
          >
            <ThumbsDown size={13} aria-hidden />
          </button>
        </div>
      </div>

      {/* 预算：合计 + 人均 + 占比条 + 图例（全部从 cost 派生；未知项按 0 累计，
          后端 cost 只有已计入项，此处如实展示为「已计入」口径） */}
      <div className="mt-3 grid gap-3 lg:grid-cols-[190px_1fr]">
        <div className="flex items-center gap-3 rounded-xl border border-[#dae7e5] bg-[#f5faf9] px-4 py-3">
          <Wallet size={18} className="shrink-0" style={{ color: TP.accent }} aria-hidden />
          <div className="min-w-0">
            <p className="text-[10px] text-[#5c7074]">旅游预计支出 · 总额</p>
            <p className="text-2xl font-bold leading-7 tabular-nums" style={{ color: TP.accent }}>
              ¥{total.toFixed(0)}
            </p>
            {brief.party_size > 1 && (
              <p className="text-[10px] text-[#5c7074]">人均约 ¥{perPerson.toFixed(0)}</p>
            )}
          </div>
        </div>

        <div className="min-w-0 rounded-xl border border-[#dae7e5] bg-white px-4 py-3">
          {total > 0 && (
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-[#f5faf9]" role="img"
              aria-label={slices.map((s) => `${s.label} ${Math.round(s.share * 100)}%`).join('，')}>
              {slices.map((s) =>
                s.share > 0 ? (
                  <span key={s.key} style={{ width: `${s.share * 100}%`, background: COST_META[s.key]?.bar ?? '#cbd5e1' }} />
                ) : null,
              )}
            </div>
          )}
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {slices.map((s) => (
              <div key={s.key} className="flex items-center gap-1.5 text-[11px]">
                <span className="h-2 w-2 rounded-full" style={{ background: COST_META[s.key]?.bar ?? '#cbd5e1' }} aria-hidden />
                <dt className="flex items-center gap-0.5 text-[#5c7074]">
                  {COST_META[s.key]?.icon}
                  {s.label}
                </dt>
                <dd className="text-[#183037]">
                  ¥{s.value.toFixed(0)}
                  {s.share > 0 && <span className="text-[#5c7074]"> · {Math.round(s.share * 100)}%</span>}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-1.5 text-[10px] text-[#5c7074]">
            已计入：门票（参考价）/ 餐饮 / 住宿 / 市内交通；门票与票价为参考值，出发前请核实；按 {brief.party_size || 1} 人估算。
          </p>
        </div>
      </div>
    </section>
  )
}

// ── 导出给页面用 ─────────────────────────────────────────────

export interface ItineraryViewProps {
  itinerary: Itinerary
  /** 追问 / 失败提示：有行程时也展示，不顶掉行程 */
  notice?: string
  finalAnswer?: string
  exporting?: boolean
  feedbackSent: '' | 'positive' | 'negative'
  onExportIcs: () => void
  onFeedback: (vote: 'positive' | 'negative') => void
}

export default function ItineraryView({
  itinerary, notice, finalAnswer, exporting = false, feedbackSent, onExportIcs, onFeedback,
}: ItineraryViewProps) {
  const dayCount = itinerary.days.length
  // 选中天：默认第 1 天；行程变短（改单后）时钳回合法范围
  const [selectedDay, setSelectedDay] = useState(1)
  const activeDay = Math.min(selectedDay, dayCount)
  const activeDayData = itinerary.days.find((d) => d.day_index === activeDay) ?? itinerary.days[0]

  return (
    <div className="space-y-4">
      {notice && (
        <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
          <p className="min-w-0 flex-1 whitespace-pre-wrap">{notice}</p>
        </div>
      )}

      <OverviewBar
        itinerary={itinerary}
        exporting={exporting}
        feedbackSent={feedbackSent}
        onExportIcs={onExportIcs}
        onFeedback={onFeedback}
      />

      <RouteMap itinerary={itinerary} />

      {/* 按天 Tab（参考 HTML 的 tp-day 交互）+ 单日时间轴 */}
      <section aria-label="按天查看">
        {dayCount > 1 && (
          <div role="tablist" aria-label="行程日期" className="mb-2.5 flex flex-wrap gap-1.5">
            {itinerary.days.map((d) => {
              const selected = d.day_index === activeDay
              return (
                <button
                  key={d.day_index}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  onClick={() => setSelectedDay(d.day_index)}
                  className={`cursor-pointer rounded-lg border px-3 py-1.5 text-xs transition-colors ${
                    selected
                      ? 'border-[#087b73] bg-[#087b73] text-white'
                      : 'border-[#dae7e5] bg-white text-[#183037] hover:bg-[#f5faf9]'
                  }`}
                >
                  第 {d.day_index} 天
                </button>
              )
            })}
          </div>
        )}
        {activeDayData && <DayCard day={activeDayData} />}
      </section>

      {itinerary.warnings.length > 0 && (
        // 出行须知默认折叠：内容全是「数据源未接入」的兜底说明，逐条平铺
        // 会把逐日行程挤下屏；degraded 的行程级警示不在这里，已挂概览条角标。
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

      {finalAnswer && (
        <details className="rounded-2xl border border-[#dae7e5] bg-white px-4 py-3 shadow-card">
          <summary className="cursor-pointer text-sm text-[#5c7074]">
            查看完整行程单（文本版，可复制发同行人）
          </summary>
          <div className="mt-3 text-sm">
            <MarkdownContent content={finalAnswer} />
          </div>
        </details>
      )}
    </div>
  )
}
