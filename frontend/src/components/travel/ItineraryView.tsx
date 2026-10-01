'use client'

/**
 * ItineraryView — 行程结果展示（2026-10-01 重做；同日二轮视觉升级）
 *
 * 原实现把「概览 + 地图 + 逐日 + 警告 + 文本版」全挤在一个 `{itinerary && ...}`
 * 里、每一条都是同款白底方框加一行灰字，用户看不出「哪天是重点、钱花在哪、
 * 现在这份行程是按什么要求排的」。重做后自上而下：概览条（按什么要求排的 +
 * 钱花在哪 + 能干什么）→ 路线图 → 逐日时间轴（时间在左、地点在右、并行标注
 * 门票/排队/用餐）→ 待确认 → 文本版。
 *
 * 二轮升级口径：全部视觉增量都从既有契约字段**派生**（travelDisplay.ts）——
 * 出发倒计时徽章、人均、费用占比条、逐日活动/在途负载条、停留时长 chip；
 * 「提醒与须知」默认折叠（全是数据源兜底说明，逐条平铺是噪音，degraded
 * 状态的警示仍保留在概览条不折叠）。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, BedDouble, CalendarPlus, Coffee, ListChecks, MapPin, Route, ThumbsDown, ThumbsUp,
  TrainFront, Ticket, UtensilsCrossed, Wallet,
} from 'lucide-react'
import { fetchRaw } from '@/api/client'
import type { Itinerary, ItineraryDay, ItineraryItem } from '@/api/travel'
import { formatDayDate, itineraryTotal, PACE_LABEL } from './planState'
import { costBreakdown, dayLoad, departureBadge, formatDuration } from './travelDisplay'
import MarkdownContent from '@/components/chat/MarkdownContent'

// ── 路线图（服务端代理，Key 不出后端） ────────────────────────

function RouteMap({ itinerary }: { itinerary: Itinerary }) {
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)

  const markers = useMemo(() => {
    const pts: string[] = []
    for (const d of itinerary.days) {
      for (const item of d.items) {
        if (item.poi && item.kind === 'visit') pts.push(`${item.poi.lat},${item.poi.lng}`)
      }
    }
    return pts
  }, [itinerary])

  useEffect(() => {
    if (markers.length === 0) return
    let revoked = ''
    let alive = true
    const qs = new URLSearchParams({ size: '640*360', zoom: '12' })
    qs.set('markers', markers.slice(0, 10).join(';'))
    fetchRaw(`/api/map/static-map?${qs.toString()}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`map ${res.status}`)
        const objectUrl = URL.createObjectURL(await res.blob())
        revoked = objectUrl
        if (alive) setUrl(objectUrl)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [markers])

  if (markers.length === 0 || failed) return null
  return (
    <figure className="animate-fade-in overflow-hidden rounded-xl border border-border-subtle bg-surface-base shadow-card">
      <figcaption className="flex items-center gap-1.5 border-b border-border-subtle px-4 py-2 text-xs font-medium text-text-secondary">
        <Route size={13} className="text-accent" aria-hidden />
        路线概览
        <span className="ml-1 font-normal text-text-muted">{markers.length} 个到访点</span>
      </figcaption>
      {url
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={url} alt="行程路线图" className="h-56 w-full object-cover" />
        : <div className="h-56 w-full animate-pulse bg-surface-hover" />}
    </figure>
  )
}

// ── 逐日时间轴 ───────────────────────────────────────────────

const KIND_STYLE: Record<string, { dot: string; label: string; icon: React.ReactNode }> = {
  visit: { dot: 'bg-accent', label: '', icon: <MapPin size={11} /> },
  meal: { dot: 'bg-orange-400', label: '用餐', icon: <UtensilsCrossed size={11} /> },
  rest: { dot: 'bg-emerald-400', label: '休息', icon: <Coffee size={11} /> },
}

function ItemTags({ item }: { item: ItineraryItem }) {
  const ticket = item.poi && item.kind === 'visit' ? item.poi.ticket_cny : 0
  const stay = formatDuration(item.minutes ?? 0)
  return (
    <span className="ml-1.5 inline-flex flex-wrap items-center gap-1 align-middle">
      {/* 停留时长：排在类型之后的第一个 chip，回答「在这待多久」 */}
      {stay && (
        <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
          停留 {stay}
        </span>
      )}
      {item.wait_minutes > 0 && (
        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
          排队 {item.wait_minutes}′
        </span>
      )}
      {ticket > 0 && (
        <span className="rounded bg-surface-hover px-1.5 py-0.5 text-[10px] text-text-muted">
          门票 ¥{ticket.toFixed(0)}
        </span>
      )}
    </span>
  )
}

function DayCard({ day }: { day: ItineraryDay }) {
  const load = dayLoad(day)
  return (
    <section className="animate-fade-in overflow-hidden rounded-xl border border-border-subtle bg-surface-base shadow-card">
      <header className="flex items-center gap-3 border-b border-border-subtle bg-surface-elevated px-4 py-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-[11px] font-bold text-accent">
          D{day.day_index}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-text-primary">第 {day.day_index} 天</h3>
          <p className="truncate text-[11px] text-text-muted">{formatDayDate(day.day_date)}</p>
        </div>
        <div className="shrink-0 text-right text-[11px] leading-4 text-text-muted">
          <div>活动 {day.active_minutes}′ · 在途 {day.transit_minutes}′</div>
          <div className="font-semibold text-text-secondary">¥{day.cost_cny.toFixed(0)}</div>
        </div>
      </header>

      {/* 负载条：活动 vs 在途的时间占比（真实派生；空日不画假条） */}
      {load.activeShare + load.transitShare > 0 && (
        <div className="flex h-1 w-full" role="img" aria-label={`活动占 ${Math.round(load.activeShare * 100)}%`}
          title={`活动 ${Math.round(load.activeShare * 100)}% · 在途 ${Math.round(load.transitShare * 100)}%`}>
          <span className="bg-accent/70" style={{ width: `${load.activeShare * 100}%` }} />
          <span className="bg-amber-300" style={{ width: `${load.transitShare * 100}%` }} />
        </div>
      )}

      <ol className="relative ml-6 border-l border-border-subtle py-3 pl-4 pr-4">
        {day.items.map((item, idx) => {
          const style = KIND_STYLE[item.kind] ?? { dot: 'bg-gray-300', label: '', icon: null }
          return (
            <li key={idx} className={`group relative rounded-lg px-2 py-1 transition-colors [margin-left:-0.5rem] hover:bg-surface-elevated ${idx === day.items.length - 1 ? '' : 'pb-2.5'}`}>
              <span
                className={`absolute -left-[21px] top-2.5 h-2 w-2 rounded-full ring-2 ring-surface-base ${style.dot}`}
                aria-hidden
              />
              <div className="flex items-baseline gap-2">
                <span className="w-[92px] shrink-0 font-mono text-[11px] tabular-nums text-text-muted">
                  {item.start}-{item.end}
                </span>
                <span className="min-w-0 flex-1 text-sm text-text-primary">
                  {item.title}
                  {style.label && (
                    <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-surface-hover px-1.5 py-0.5 text-[10px] text-text-muted">
                      {style.icon}
                      {style.label}
                    </span>
                  )}
                  <ItemTags item={item} />
                </span>
              </div>
              {item.note && <p className="mt-0.5 pl-[100px] text-[11px] text-text-muted">{item.note}</p>}
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
 * validating / ready / degraded / needs_user_decision。
 * ready 是常态不挂角标；degraded 表示部分外部数据没取到、行程仍可用。
 */
const STATUS_LABEL: Record<string, string> = {
  ready: '',
  validating: '',
  degraded: '部分数据未取到，行程已按降级结果生成',
  needs_user_decision: '有需要你拍板的点',
}

/** 费用分类的展示形态（条形颜色 + 图例图标）。顺序 = costBreakdown 的切片顺序。 */
const COST_META: Record<string, { bar: string; icon: React.ReactNode }> = {
  tickets: { bar: 'bg-accent', icon: <Ticket size={12} aria-hidden /> },
  meals: { bar: 'bg-orange-400', icon: <UtensilsCrossed size={12} aria-hidden /> },
  lodging: { bar: 'bg-emerald-400', icon: <BedDouble size={12} aria-hidden /> },
  transit: { bar: 'bg-gray-300', icon: <TrainFront size={12} aria-hidden /> },
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

  // 需求摘要 chips：右侧「对话改行程」抽屉要改的就是这几项，看不见就没法改。
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
    <section className="animate-fade-in rounded-2xl border border-border-subtle bg-surface-base p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-text-primary">
              {brief.destination || '未命名目的地'} 行程
            </h2>
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[11px] font-medium text-accent">
              {itinerary.days.length} 天
            </span>
            <span className="rounded-full bg-surface-hover px-2 py-0.5 text-[11px] text-text-muted">
              v{itinerary.plan_version}
            </span>
            {/* 出发倒计时：真实派生；无日期不渲染（「未定日期」在逐日卡里已有） */}
            {badge && (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-[11px] font-medium text-white">
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
            {/* 出发日期放进摘要首位（chip 形式，与可改字段一一对上） */}
            {brief.start_date && (
              <span className="rounded-full border border-border-subtle bg-surface-elevated px-2 py-0.5 text-[11px] text-text-secondary">
                {brief.start_date} 出发
              </span>
            )}
            {meta.map((m) => (
              <span
                key={m}
                className="rounded-full border border-border-subtle bg-surface-elevated px-2 py-0.5 text-[11px] text-text-secondary"
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
            className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border-subtle px-2.5 py-1.5
              text-xs text-text-secondary transition-colors hover:border-accent/40 hover:text-text-primary
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
                : 'border-border-subtle text-text-secondary hover:border-accent/40 hover:text-text-primary'
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
                : 'border-border-subtle text-text-secondary hover:border-accent/40 hover:text-text-primary'
            }`}
          >
            <ThumbsDown size={13} aria-hidden />
          </button>
        </div>
      </div>

      {/* 费用：合计 + 人均 + 占比条 + 图例（全部从 cost 派生） */}
      <div className="mt-3 grid gap-3 sm:grid-cols-[150px_1fr]">
        <div className="flex items-center gap-3 rounded-xl bg-accent/10 px-4 py-3">
          <Wallet size={18} className="shrink-0 text-accent" aria-hidden />
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-accent">预算合计</p>
            <p className="text-xl font-bold leading-6 text-accent">¥{total.toFixed(0)}</p>
            {brief.party_size > 1 && (
              <p className="text-[10px] text-accent/80">人均约 ¥{perPerson.toFixed(0)}</p>
            )}
          </div>
        </div>

        <div className="min-w-0 rounded-xl border border-border-subtle bg-surface-elevated px-4 py-3">
          {total > 0 && (
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-hover" role="img"
              aria-label={slices.map((s) => `${s.label} ${Math.round(s.share * 100)}%`).join('，')}>
              {slices.map((s) =>
                s.share > 0 ? (
                  <span key={s.key} className={COST_META[s.key]?.bar ?? 'bg-gray-300'} style={{ width: `${s.share * 100}%` }} />
                ) : null,
              )}
            </div>
          )}
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {slices.map((s) => (
              <div key={s.key} className="flex items-center gap-1.5 text-[11px]">
                <span className={`h-2 w-2 rounded-full ${COST_META[s.key]?.bar ?? 'bg-gray-300'}`} aria-hidden />
                <dt className="flex items-center gap-0.5 text-text-muted">
                  {COST_META[s.key]?.icon}
                  {s.label}
                </dt>
                <dd className="text-text-secondary">
                  ¥{s.value.toFixed(0)}
                  {s.share > 0 && <span className="text-text-muted"> · {Math.round(s.share * 100)}%</span>}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-1.5 text-[10px] text-text-muted">
            门票与票价为参考值，出发前请核实；合计按 {brief.party_size || 1} 人估算。
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
  return (
    <div className="space-y-4">
      {notice && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
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

      <div className="space-y-3">
        {itinerary.days.map((day) => <DayCard key={day.day_index} day={day} />)}
      </div>

      {itinerary.warnings.length > 0 && (
        // 出行须知默认折叠：内容全是「数据源未接入」的兜底说明，逐条平铺
        // 会把逐日行程挤下屏；degraded 的行程级警示不在这里，已挂概览条角标。
        <details className="group animate-fade-in rounded-xl border border-border-subtle bg-surface-base shadow-card">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-3 text-sm font-semibold text-text-secondary transition-colors hover:text-text-primary [&::-webkit-details-marker]:hidden">
            <ListChecks size={14} className="text-amber-600" aria-hidden />
            出行须知
            <span className="text-[11px] font-normal text-text-muted">{itinerary.warnings.length} 条</span>
            <span
              className="ml-auto text-text-muted transition-transform duration-200 group-open:rotate-180"
              aria-hidden
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
            </span>
          </summary>
          <ul className="list-inside list-disc space-y-1 border-t border-border-subtle px-4 py-3 text-sm text-text-secondary">
            {itinerary.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}

      {finalAnswer && (
        <details className="rounded-xl border border-border-subtle bg-surface-base px-4 py-3 shadow-card">
          <summary className="cursor-pointer text-sm text-text-muted">
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
