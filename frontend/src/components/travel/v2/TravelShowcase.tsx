'use client'

/**
 * TravelShowcase — 首页「推荐行程」能力橱窗（travel-value-showcase B4）。
 *
 * 定位（用户口径）：让新用户最快看懂平台能干嘛的功能橱窗，不是「最近行程」
 * 翻版。数据源唯一 = 服务端真实已保存行程（fetchTravelTripsV2），禁止任何
 * 种子/演示数据垫展示；筛选 = brief.tier（档位）× brief.interests（风格）
 * 纯前端过滤，任一组合筛空如实空态并给规划入口（红线 1）。
 *
 * 档位展示名（红线 2）：「平价」（完整标签「平价出行」）；「穷游」只作为
 * 输入触发词收录在词表，展示侧零出现。预算口径照旧「预估 · 未核验」。
 */
import { useMemo, useState } from 'react'
import { CalendarDays, Star, Ticket, Users } from 'lucide-react'
import type { TravelTripV2, TripTierV2 } from '@/api/travelV2'
import { travelDestinationImage } from './TravelTemplateCard'

const TIER_CHIP_LABELS: Record<TripTierV2, string> = {
  budget: '平价',
  economy: '经济',
  comfortable: '舒适',
}
const TIER_FULL_LABELS: Record<TripTierV2, string> = {
  budget: '平价出行',
  economy: '经济实用型',
  comfortable: '舒适均衡型',
}

/** 免费门票数：仅统计服务端明确票价 = 0 的已核验事实；null/未知一律不计。 */
function freeTicketCount(trip: TravelTripV2): number {
  return trip.document.days.reduce((sum, day) => sum + day.items.filter(
    (item) => item.place?.facts.ticket_price_cny === 0,
  ).length, 0)
}

/** 平均评分：仅聚合服务端给出的非空评分；一个都没有 → null（不编造）。 */
function averageRating(trip: TravelTripV2): number | null {
  const ratings: number[] = []
  for (const day of trip.document.days) {
    for (const item of day.items) {
      const rating = item.place?.facts.rating
      if (typeof rating === 'number' && Number.isFinite(rating)) ratings.push(rating)
    }
  }
  if (ratings.length === 0) return null
  return Math.round((ratings.reduce((a, b) => a + b, 0) / ratings.length) * 10) / 10
}

export default function TravelShowcase({ trips, loading }: {
  trips: TravelTripV2[]
  loading: boolean
}) {
  const [tier, setTier] = useState<'all' | TripTierV2>('all')
  const [style, setStyle] = useState<string>('all')

  // 风格 chips 只来自真实行程的 brief.interests（真实数据唯一，不预置词表）
  const styles = useMemo(() => {
    const counter = new Map<string, number>()
    for (const trip of trips) {
      for (const interest of trip.document.brief.interests) {
        counter.set(interest, (counter.get(interest) ?? 0) + 1)
      }
    }
    return [...counter.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name)
  }, [trips])

  const filtered = useMemo(() => trips.filter((trip) => {
    const brief = trip.document.brief
    if (tier !== 'all' && brief.tier !== tier) return false
    if (style !== 'all' && !brief.interests.includes(style)) return false
    return true
  }), [trips, tier, style])

  return (
    <section className="mt-8 md:mt-10" aria-label="推荐行程">
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold text-[#4a82c5]">真实行程示范</p>
          <h2 className="mt-1 text-lg font-bold tracking-[-.025em] text-[#17283f] md:text-[21px]">推荐行程</h2>
          <p className="mt-1 text-[11px] text-[#8292a6]">AI 真实排出来的行程：预算构成、免费门票、评分都是真实数据口径。</p>
        </div>
      </div>

      {/* 档位 × 风格筛选（纯前端过滤，不改任何服务端状态） */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-[10px] text-[#8a9bb0]">档位</span>
        {(['all', 'budget', 'economy', 'comfortable'] as const).map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={tier === value}
            onClick={() => setTier(value)}
            className={`rounded-full border px-3 py-1.5 text-[11px] font-medium transition ${
              tier === value
                ? 'border-[#8db6f8] bg-[#f0f6ff] text-[#2878f5]'
                : 'border-[#e4ebf3] bg-white text-[#536b86] hover:border-[#bfd6f5]'
            }`}
          >
            {value === 'all' ? '全部' : TIER_CHIP_LABELS[value]}
          </button>
        ))}
        {styles.length > 0 && (
          <>
            <span className="ml-2 text-[10px] text-[#8a9bb0]">风格</span>
            {['all', ...styles].map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={style === value}
                onClick={() => setStyle(value)}
                className={`rounded-full border px-3 py-1.5 text-[11px] font-medium transition ${
                  style === value
                    ? 'border-[#8db6f8] bg-[#f0f6ff] text-[#2878f5]'
                    : 'border-[#e4ebf3] bg-white text-[#536b86] hover:border-[#bfd6f5]'
                }`}
              >
                {value === 'all' ? '全部' : value}
              </button>
            ))}
          </>
        )}
      </div>

      {loading ? (
        <p role="status" className="rounded-xl border border-[#e6edf5] bg-white px-4 py-6 text-center text-xs text-[#8292a6]">正在加载推荐行程…</p>
      ) : filtered.length === 0 ? (
        trips.length === 0 ? (
          <div className="rounded-[20px] border border-dashed border-[#cfdeef] bg-white/70 p-6 text-center">
            <p className="text-sm font-semibold text-[#465e7a]">还没有真实行程示范</p>
            <p className="mt-1 text-xs text-[#8a9bb0]">从上方描述旅行需求，规划完成后会自动出现在这里。</p>
            <a href="/travel/itineraries/new" className="mt-3 inline-flex h-9 items-center rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white transition hover:bg-[#1769e9]">去规划第一份行程</a>
          </div>
        ) : (
          <div className="rounded-[20px] border border-dashed border-[#cfdeef] bg-white/70 p-6 text-center">
            <p className="text-sm font-semibold text-[#465e7a]">还没有这类行程</p>
            <p className="mt-1 text-xs text-[#8a9bb0]">换个档位或风格组合，或者自己排一份。</p>
            <a href="/travel/itineraries/new" className="mt-3 inline-flex h-9 items-center rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white transition hover:bg-[#1769e9]">去规划</a>
          </div>
        )
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((trip) => <ShowcaseCard key={trip.trip_id} trip={trip} />)}
        </div>
      )}
    </section>
  )
}

function ShowcaseCard({ trip }: { trip: TravelTripV2 }) {
  const brief = trip.document.brief
  const destination = brief.destination
  const image = travelDestinationImage(destination)
  const days = brief.day_count
  const people = (brief.travelers.adults ?? 0) + (brief.travelers.children ?? 0)
  const budget = trip.document.totals.estimated_cost ?? brief.budget.amount
  const freeTickets = freeTicketCount(trip)
  const rating = averageRating(trip)
  const tierLabel = TIER_FULL_LABELS[brief.tier] ?? '经济实用型'
  const styleLabel = brief.interests.slice(0, 2).join(' · ') || tierLabel

  return (
    <a
      href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`}
      className="group block min-w-0 overflow-hidden rounded-[22px] border border-[#e6edf5] bg-white transition hover:-translate-y-0.5 hover:border-[#c7dcf8] hover:shadow-[0_12px_30px_rgba(30,72,125,.1)] motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5]"
    >
      <div className="relative h-[150px] overflow-hidden md:h-[178px]">
        <img src={image} alt={`${destination}行程封面`} loading="lazy" className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover:scale-100" />
        <div className="absolute inset-0 bg-gradient-to-t from-[#122944]/55 to-transparent" />
        <span className="absolute left-3 top-3 rounded-full bg-white/90 px-2.5 py-1 text-[10px] font-semibold text-[#376aab]">{days}天{Math.max(0, days - 1)}晚</span>
        <span className="absolute right-3 top-3 rounded-full bg-[#2878f5]/90 px-2.5 py-1 text-[10px] font-semibold text-white">{tierLabel}</span>
        <span className="absolute bottom-3 left-3 text-lg font-semibold text-white">{destination}</span>
        <span className="absolute bottom-3 right-3 max-w-[55%] truncate text-[10px] text-white/90">{styleLabel}</span>
      </div>
      <div className="p-3.5">
        <p className="truncate text-sm font-semibold text-[#243953]">{trip.title || `${destination}行程`}</p>
        {/* 能力信号行：真实预算构成 / 免费门票 / 评分 + 诚实披露（预估 · 未核验） */}
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-[#7a8ba0]">
          <span className="inline-flex items-center gap-1"><Ticket size={11} aria-hidden />免费门票 {freeTickets} 处</span>
          {rating !== null && <span className="inline-flex items-center gap-1"><Star size={11} aria-hidden />均分 {rating}</span>}
        </p>
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#edf1f6] pt-2.5 text-[9px] text-[#8797aa]">
          <span className="inline-flex items-center gap-1"><CalendarDays size={11} aria-hidden />{days} 天</span>
          <span className="inline-flex items-center gap-1"><Users size={11} aria-hidden />{people} 人</span>
          <span>{budget === null || budget === undefined ? '预算待设置' : `预算约 ¥${budget.toLocaleString()}`}</span>
          <span className="rounded-full bg-[#f4f6f9] px-1.5 py-0.5 text-[8px] text-[#9aa8b8]">预估 · 未核验</span>
        </p>
      </div>
    </a>
  )
}
