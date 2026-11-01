'use client'

import { useState } from 'react'
import { LoaderCircle, Sparkles, X } from 'lucide-react'
import {
  addTravelMealV2,
  autoOptimizeTravelTripV2,
  createTravelV2IdempotencyKey,
  selectTravelLodgingV2,
  type LodgingArrangementV2,
  type MerchantSnapshotV2,
  type TravelAutoOptimizeResultV2,
  type TravelTripV2,
} from '@/api/travelV2'

/**
 * F04 自动优选建议确认卡（只读编排 → 用户确认 → 既有单条 selection 端点
 * 逐条幂等选入；本组件零新增写路径）。
 *
 * 建议行字段由后端 auto-optimize 与搜索 Tab 同源映射（含完整快照字段），
 * 选入构参不臆造。餐饮目标餐位（天/时段）由用户逐条可改，默认按行程
 * 空缺餐位顺序填充；住宿直接设为行程住宿参考（booking_status=not_booked）。
 */

interface MealPick {
  selection_id: string
  merchant_id: string | null
  name: string
  rating: number | null
  avg_cost_cny: number | null
  address: string | null
  lat: number | null
  lng: number | null
  open_status: string | null
  open_time_today: string | null
  source: string
  observed_at: string
  reason: string
}

interface LodgingPick {
  selection_id: string
  merchant_id: string | null
  name: string
  city: string | null
  address: string | null
  lat: number | null
  lng: number | null
  check_in: string | null
  check_out: string | null
  nights: number | null
  price_total: string | null
  currency: string | null
  nightly_price_cny: number | null
  tier: string | null
  availability: string
  booking_status: string
  source: string | null
  observed_at: string | null
  deep_link: string | null
  reason: string
}

/** 跳转型预订链接：仅透传后端 deeplink 白名单门放行的 https 链接（双保险）。 */
function bookableLink(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const link = value.trim()
  return link.startsWith('https://') ? link : null
}

function merchantSnapshot(pick: MerchantPickFields): MerchantSnapshotV2 {
  return {
    merchant_id: pick.merchant_id || pick.selection_id,
    name: pick.name,
    address: pick.address,
    lat: pick.lat,
    lng: pick.lng,
    rating: pick.rating,
    open_status: pick.open_status,
    open_time_today: pick.open_time_today,
    avg_cost_cny: pick.avg_cost_cny,
    source: pick.source || 'amap',
    observed_at: pick.observed_at,
  }
}

type MerchantPickFields = {
  selection_id: string
  merchant_id: string | null
  name: string
  rating: number | null
  address: string | null
  lat: number | null
  lng: number | null
  open_status: string | null
  open_time_today: string | null
  avg_cost_cny: number | null
  source: string
  observed_at: string
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 建议行诚实解析：缺 name/selection_id 的条目跳过（不渲染脏数据）。 */
function parseMealPick(row: Record<string, unknown>): MealPick | null {
  const name = stringOrNull(row.name)
  const selectionId = stringOrNull(row.selection_id)
  if (!name || !selectionId) return null
  return {
    selection_id: selectionId,
    merchant_id: stringOrNull(row.merchant_id),
    name,
    rating: numberOrNull(row.rating),
    avg_cost_cny: numberOrNull(row.avg_cost_cny),
    address: stringOrNull(row.address),
    lat: numberOrNull(row.lat),
    lng: numberOrNull(row.lng),
    open_status: stringOrNull(row.open_status),
    open_time_today: stringOrNull(row.open_time_today),
    source: stringOrNull(row.source) ?? 'amap',
    observed_at: stringOrNull(row.observed_at) ?? new Date().toISOString(),
    reason: stringOrNull(row.reason) ?? '',
  }
}

function parseLodgingPick(row: Record<string, unknown>): LodgingPick | null {
  const name = stringOrNull(row.name)
  const selectionId = stringOrNull(row.selection_id)
  if (!name || !selectionId) return null
  return {
    selection_id: selectionId,
    merchant_id: stringOrNull(row.merchant_id),
    name,
    city: stringOrNull(row.city),
    address: stringOrNull(row.address),
    lat: numberOrNull(row.lat),
    lng: numberOrNull(row.lng),
    check_in: stringOrNull(row.check_in),
    check_out: stringOrNull(row.check_out),
    nights: numberOrNull(row.nights),
    price_total: stringOrNull(row.price_total),
    currency: stringOrNull(row.currency),
    nightly_price_cny: numberOrNull(row.nightly_price_cny),
    tier: stringOrNull(row.tier),
    availability: stringOrNull(row.availability) ?? 'unknown',
    booking_status: stringOrNull(row.booking_status) ?? 'not_booked',
    source: stringOrNull(row.source),
    observed_at: stringOrNull(row.observed_at),
    deep_link: bookableLink(row.deep_link),
    reason: stringOrNull(row.reason) ?? '',
  }
}

/** 默认餐位：按天序填充 lunch → dinner 空缺（确定性，用户可改）。 */
function defaultMealTargets(dayCount: number, mealCount: number): Array<{ dayIndex: number; period: 'lunch' | 'dinner' }> {
  const targets: Array<{ dayIndex: number; period: 'lunch' | 'dinner' }> = []
  let index = 0
  for (let round = 0; round < dayCount && index < mealCount; round += 1) {
    for (const period of ['lunch', 'dinner'] as const) {
      if (index >= mealCount) break
      targets.push({ dayIndex: round, period })
      index += 1
    }
  }
  while (index < mealCount) {
    targets.push({ dayIndex: dayCount - 1, period: 'dinner' })
    index += 1
  }
  return targets
}

export default function AutoOptimizePanel({
  trip,
  disabled,
  onTripSaved,
  onClose,
}: {
  trip: TravelTripV2
  disabled: boolean
  onTripSaved: (trip: TravelTripV2, summary: string) => void
  onClose: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<TravelAutoOptimizeResultV2 | null>(null)
  const [targets, setTargets] = useState<Array<{ dayIndex: number; period: 'lunch' | 'dinner' }>>([])
  const [applied, setApplied] = useState<string[]>([])

  async function load() {
    if (loading || applying) return
    setLoading(true)
    setError('')
    setResult(null)
    setApplied([])
    try {
      const data = await autoOptimizeTravelTripV2(trip.trip_id)
      const meals = ((data.suggestions.meals ?? []) as Record<string, unknown>[])
        .map(parseMealPick)
        .filter((pick): pick is MealPick => pick !== null)
      setResult(data)
      setTargets(defaultMealTargets(trip.document.brief.day_count, meals.length))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '自动优选暂时不可用，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  async function applyAll() {
    if (!result || applying || disabled) return
    setApplying(true)
    setError('')
    try {
      let current = trip
      const meals = ((result.suggestions.meals ?? []) as Record<string, unknown>[])
        .map(parseMealPick)
        .filter((pick): pick is MealPick => pick !== null)
      const lodgings = ((result.suggestions.lodgings ?? []) as Record<string, unknown>[])
        .map(parseLodgingPick)
        .filter((pick): pick is LodgingPick => pick !== null)
      for (let index = 0; index < meals.length; index += 1) {
        const pick = meals[index]
        if (applied.includes(pick.selection_id)) continue
        const target = targets[index] ?? { dayIndex: 0, period: 'lunch' as const }
        const day = current.document.days[target.dayIndex]
        if (!day) throw new Error('目标天不存在，选入中止。已完成的选入保持有效。')
        const input = {
          expected_revision: current.revision,
          day_id: day.day_id,
          meal_period: target.period,
          selection_id: pick.selection_id,
          merchant: merchantSnapshot(pick),
        }
        const savedTrip = await addTravelMealV2(current.trip_id, input, createTravelV2IdempotencyKey())
        current = { ...current, revision: savedTrip.revision, document: savedTrip.document }
        setApplied((prev) => [...prev, pick.selection_id])
      }
      for (const pick of lodgings) {
        if (applied.includes(pick.selection_id)) continue
        const lodging: LodgingArrangementV2 = {
          selection_id: pick.selection_id,
          merchant: {
            merchant_id: pick.merchant_id || pick.selection_id,
            name: pick.name,
            address: pick.address,
            lat: pick.lat,
            lng: pick.lng,
            rating: null,
            open_status: null,
            open_time_today: null,
            avg_cost_cny: null,
            source: pick.source || '-',
            observed_at: pick.observed_at ?? new Date().toISOString(),
          },
          check_in: pick.check_in ?? current.document.brief.start_date ?? '',
          check_out: pick.check_out ?? current.document.brief.start_date ?? '',
          source: pick.source || '-',
          queried_at: pick.observed_at ?? new Date().toISOString(),
          verification: 'unknown',
          booking_status: 'not_booked',
          nightly_price_cny: pick.nightly_price_cny,
        }
        if (!lodging.check_in || !lodging.check_out || lodging.check_out <= lodging.check_in) {
          throw new Error('住宿建议缺少有效入住日期，无法选入。其余已完成的选入保持有效。')
        }
        const input = { expected_revision: current.revision, lodging }
        const savedTrip = await selectTravelLodgingV2(current.trip_id, input, createTravelV2IdempotencyKey())
        current = { ...current, revision: savedTrip.revision, document: savedTrip.document }
        setApplied((prev) => [...prev, pick.selection_id])
      }
      onTripSaved(current, `自动优选建议已按既有 selections 端点逐条选入（v${current.revision}）。`)
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : '未知错误'
      setError(`选入未全部完成：${detail}。已完成的部分保持有效。`)
    } finally {
      setApplying(false)
    }
  }

  const meals = ((result?.suggestions.meals ?? []) as Record<string, unknown>[])
    .map(parseMealPick)
    .filter((pick): pick is MealPick => pick !== null)
  const lodgings = ((result?.suggestions.lodgings ?? []) as Record<string, unknown>[])
    .map(parseLodgingPick)
    .filter((pick): pick is LodgingPick => pick !== null)

  return <section aria-label="自动优选建议" className="mx-3 mt-3 rounded-xl border border-[#cfe0f5] bg-[#f6faff] p-4 text-sm leading-5 lg:mx-4">
    <div className="flex items-center justify-between gap-2">
      <p className="flex items-center gap-1.5 font-semibold text-[#263c57]"><Sparkles size={14} className="text-[#2878f5]" aria-hidden />自动优选建议</p>
      <div className="flex items-center gap-2">
        {!result && !loading && <button type="button" disabled={disabled} onClick={() => void load()} className="min-h-9 rounded-lg bg-[#2878f5] px-3 text-xs font-semibold text-white disabled:opacity-50">生成建议</button>}
        {loading && <span className="flex items-center gap-1.5 text-xs text-[#526984]"><LoaderCircle size={13} className="animate-spin" aria-hidden />正在聚合查询…</span>}
        <button type="button" onClick={onClose} aria-label="关闭自动优选建议" className="rounded-lg p-1.5 text-[#71839b] hover:bg-white"><X size={15} aria-hidden /></button>
      </div>
    </div>
    {result && <div className="mt-3 space-y-3 text-xs">
      {meals.length > 0 && <div>
        <p className="font-semibold text-[#2b405b]">餐饮建议（评分降序）</p>
        <ul className="mt-1.5 space-y-1.5">
          {meals.map((pick, index) => {
            const target = targets[index] ?? { dayIndex: 0, period: 'lunch' as const }
            const isApplied = applied.includes(pick.selection_id)
            return <li key={pick.selection_id} className="rounded-lg border border-[#e2ecf7] bg-white p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0"><p className="truncate font-semibold text-[#2b405b]">{pick.name}</p><p className="mt-0.5 truncate text-[10px] text-[#7d8ea3]">{pick.reason}{pick.rating != null ? ` · 评分 ${pick.rating}` : ''}{pick.avg_cost_cny != null ? ` · 人均 ¥${pick.avg_cost_cny}` : ''}{pick.address ? ` · ${pick.address}` : ''}</p></div>
                {isApplied && <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-1 text-[9px] font-medium text-emerald-700">已选入</span>}
              </div>
              {!isApplied && <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-[#5e748f]">
                <label className="sr-only" htmlFor={`opt-day-${index}`}>目标天</label>
                <select id={`opt-day-${index}`} value={target.dayIndex} disabled={applying}
                  onChange={(event) => setTargets((current) => current.map((item, i) => i === index ? { ...item, dayIndex: Number(event.target.value) } : item))}
                  className="rounded-md border border-[#dbe6f3] bg-white px-1.5 py-1">
                  {trip.document.days.map((day, dayIndex) => <option key={day.day_id} value={dayIndex}>第 {dayIndex + 1} 天</option>)}
                </select>
                <label className="sr-only" htmlFor={`opt-period-${index}`}>餐段</label>
                <select id={`opt-period-${index}`} value={target.period} disabled={applying}
                  onChange={(event) => setTargets((current) => current.map((item, i) => i === index ? { ...item, period: event.target.value as 'lunch' | 'dinner' } : item))}
                  className="rounded-md border border-[#dbe6f3] bg-white px-1.5 py-1">
                  <option value="lunch">午餐</option>
                  <option value="dinner">晚餐</option>
                </select>
              </div>}
            </li>
          })}
        </ul>
      </div>}
      {lodgings.length > 0 && <div>
        <p className="font-semibold text-[#2b405b]">住宿建议（价格升序 · 只作参考，预订走确认门）</p>
        <ul className="mt-1.5 space-y-1.5">
          {lodgings.map((pick) => {
            const bookingLink = bookableLink(pick.deep_link)
            return <li key={pick.selection_id} className="rounded-lg border border-[#e2ecf7] bg-white p-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0"><p className="truncate font-semibold text-[#2b405b]">{pick.name}</p><p className="mt-0.5 truncate text-[10px] text-[#7d8ea3]">{pick.reason}{pick.nightly_price_cny != null ? ` · ¥${pick.nightly_price_cny}/晚` : ''}{pick.tier ? ` · ${pick.tier} 档` : ''}{pick.availability ? ` · ${pick.availability}` : ''}</p></div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {bookingLink && <a href={bookingLink} target="_blank" rel="noopener noreferrer" aria-label={`跳转供应商官方页面预订${pick.name}`} className="inline-flex min-h-8 items-center rounded-lg bg-[#087b73] px-2.5 text-[10px] font-medium text-white hover:bg-[#06655f]">去预订</a>}
                  {applied.includes(pick.selection_id) ? <span className="rounded-full bg-emerald-50 px-2 py-1 text-[9px] font-medium text-emerald-700">已选入</span> : null}
                </div>
              </div>
            </li>
          })}
        </ul>
        {lodgings.some((pick) => bookableLink(pick.deep_link)) && <p className="mt-1.5 text-[10px] leading-4 text-[#8493a6]">「去预订」跳转供应商官方页面完成下单，由你自行操作；平台不代订、不记录订单状态。</p>}
      </div>}
      {result.disclosures.length > 0 && <ul className="space-y-1 text-[10px] leading-4 text-amber-800">{result.disclosures.map((item, index) => <li key={index}>{item}</li>)}</ul>}
      <p className="text-[10px] text-[#8493a6]">{result.apply_hint}</p>
      {(meals.length > 0 || lodgings.length > 0) && <button type="button" disabled={applying || disabled} onClick={() => void applyAll()} className="min-h-10 w-full rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white disabled:opacity-60">
        {applying ? '正在逐条选入…' : '确认并逐条选入'}
      </button>}
    </div>}
    {error && <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}
  </section>
}
