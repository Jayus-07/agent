'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, MapPin, Search, TrainFront, Utensils, Hotel, Check, CircleAlert, LoaderCircle } from 'lucide-react'
import {
  addTravelMealV2,
  searchTravelFoodV2,
  searchTravelHotelsV2,
  searchTravelTrainsV2,
  selectTravelLodgingV2,
  selectTravelTrainV2,
  type IntercityTrainArrangementV2,
  type LodgingArrangementV2,
  type MerchantSearchResultV2,
  type MerchantSnapshotV2,
  type TravelSearchResponseV2,
  type TravelSearchStatusV2,
  type TravelTripV2,
  type TrainSearchResultV2,
} from '@/api/travelV2'

export type TravelSearchKind = 'trip' | 'food' | 'hotel' | 'train'
export interface TravelSearchEvent {
  kind: Exclude<TravelSearchKind, 'trip'>
  status: 'querying' | TravelSearchStatusV2
  source: string
  message: string
  queriedAt?: string
}

const searchOptions: Array<{ kind: Exclude<TravelSearchKind, 'trip'>; label: string; icon: typeof Hotel }> = [
  { kind: 'food', label: '美食', icon: Utensils },
  { kind: 'hotel', label: '酒店', icon: Hotel },
  { kind: 'train', label: '车次', icon: TrainFront },
]

const statusText: Record<string, string> = {
  success: '查询成功',
  no_results: '无结果',
  business_failure: '查询失败',
  network_timeout: '网络超时',
  provider_unavailable: '数据源不可用',
  disabled: '能力未启用',
}

export default function TravelSearchPanel({
  initialKind = 'food',
  trip,
  showTabs = true,
  onTripSaved,
  onLocationSelect,
  onSearchEvent,
}: {
  initialKind?: Exclude<TravelSearchKind, 'trip'>
  trip?: TravelTripV2
  showTabs?: boolean
  onTripSaved?: (trip: TravelTripV2, summary: string) => void
  onLocationSelect?: (location: { name: string; lat: number; lng: number } | null) => void
  onSearchEvent?: (event: TravelSearchEvent) => void
}) {
  const [kind, setKind] = useState<Exclude<TravelSearchKind, 'trip'>>(initialKind)
  const [city, setCity] = useState(trip?.document.brief.destination ?? '')
  const [fromStation, setFromStation] = useState(trip?.document.brief.origin ?? '')
  const [toStation, setToStation] = useState(trip?.document.brief.destination ?? '')
  const [travelDate, setTravelDate] = useState(trip?.document.brief.start_date ?? '')
  const [checkIn, setCheckIn] = useState(trip?.document.brief.start_date ?? '')
  const [checkOut, setCheckOut] = useState(() => tripEndDate(trip))
  const [dayId, setDayId] = useState(trip?.document.days[0]?.day_id ?? '')
  const [mealPeriod, setMealPeriod] = useState<'lunch' | 'dinner'>('lunch')
  const [querying, setQuerying] = useState(false)
  const [response, setResponse] = useState<TravelSearchResponseV2 | null>(null)
  const [requestError, setRequestError] = useState('')
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [savedMessage, setSavedMessage] = useState('')

  useEffect(() => {
    setKind(initialKind)
    setResponse(null)
    setRequestError('')
    setSelectedIndex(null)
  }, [initialKind])

  useEffect(() => {
    if (!trip) return
    setCity(trip.document.brief.destination)
    setFromStation(trip.document.brief.origin)
    setToStation(trip.document.brief.destination)
    setTravelDate(trip.document.brief.start_date ?? '')
    setCheckIn(trip.document.brief.start_date ?? '')
    setCheckOut(tripEndDate(trip))
    setDayId(trip.document.days[0]?.day_id ?? '')
  }, [trip?.trip_id])

  const selected = selectedIndex === null ? null : response?.results[selectedIndex] ?? null

  async function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (querying) return
    setQuerying(true)
    setResponse(null)
    setRequestError('')
    setSaveError('')
    setSavedMessage('')
    setSelectedIndex(null)
    onLocationSelect?.(null)
    onSearchEvent?.({
      kind, status: 'querying', source: kind === 'train' ? '12306' : 'amap',
      message: `正在查询${kind === 'food' ? '美食' : kind === 'hotel' ? '酒店' : '动车'}…`,
    })
    try {
      const result = kind === 'food'
        ? await searchTravelFoodV2(city.trim())
        : kind === 'hotel'
          ? await searchTravelHotelsV2(city.trim())
          : await searchTravelTrainsV2({
            from_station: fromStation.trim(),
            to_station: toStation.trim(),
            travel_date: travelDate,
          })
      setResponse(result)
      onSearchEvent?.({
        kind, status: result.status, source: result.source, message: result.message,
        queriedAt: result.queried_at,
      })
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : ''
      const status: TravelSearchStatusV2 = /timeout|超时|aborted/i.test(message)
        ? 'network_timeout'
        : /未启用|disabled/i.test(message)
          ? 'disabled'
          : /unavailable|不可用/i.test(message)
            ? 'provider_unavailable'
            : 'business_failure'
      const notice = status === 'network_timeout' ? '网络超时，请稍后重试。' : '查询请求未完成，行程没有修改。请检查网络后重试。'
      setRequestError(notice)
      onSearchEvent?.({ kind, status, source: kind === 'train' ? '12306' : 'amap', message: notice, queriedAt: new Date().toISOString() })
    } finally {
      setQuerying(false)
    }
  }

  function openResult(index: number) {
    setSelectedIndex(index)
    const item = response?.results[index]
    if (item && kind !== 'train') {
      const merchant = item as MerchantSearchResultV2
      if (typeof merchant.lat === 'number' && typeof merchant.lng === 'number') {
        onLocationSelect?.({ name: merchant.name, lat: merchant.lat, lng: merchant.lng })
      } else {
        onLocationSelect?.(null)
      }
    } else {
      onLocationSelect?.(null)
    }
  }

  async function addMeal(merchant: MerchantSearchResultV2) {
    if (!trip || !dayId || saving) return
    setSaving(true)
    setSaveError('')
    setSavedMessage('')
    try {
      const merchantSnapshot: MerchantSnapshotV2 = {
        merchant_id: merchant.merchant_id,
        name: merchant.name,
        address: merchant.address,
        lat: merchant.lat,
        lng: merchant.lng,
        rating: merchant.rating,
        open_status: merchant.open_status,
        open_time_today: merchant.open_time_today,
        avg_cost_cny: merchant.avg_cost_cny,
        source: merchant.source,
        observed_at: merchant.queried_at,
      }
      const result = await addTravelMealV2(trip.trip_id, {
        expected_revision: trip.revision,
        day_id: dayId,
        meal_period: mealPeriod,
        selection_id: merchant.selection_id,
        merchant: merchantSnapshot,
      })
      onTripSaved?.({ ...trip, revision: result.revision, document: result.document }, `${mealPeriod === 'lunch' ? '午餐' : '晚餐'}已加入并保存。`)
      setSavedMessage('已加入行程并保存。')
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : '保存失败，原行程保持不变。')
    } finally {
      setSaving(false)
    }
  }

  async function selectLodging(merchant: MerchantSearchResultV2) {
    if (!trip || saving) return
    setSaving(true)
    setSaveError('')
    setSavedMessage('')
    try {
      const lodging: LodgingArrangementV2 = {
        selection_id: merchant.selection_id,
        merchant: {
          merchant_id: merchant.merchant_id,
          name: merchant.name,
          address: merchant.address,
          lat: merchant.lat,
          lng: merchant.lng,
          rating: merchant.rating,
          open_status: merchant.open_status,
          open_time_today: merchant.open_time_today,
          avg_cost_cny: merchant.avg_cost_cny,
          source: merchant.source,
          observed_at: merchant.queried_at,
        },
        check_in: checkIn,
        check_out: checkOut,
        source: merchant.source,
        queried_at: merchant.queried_at,
        verification: 'unknown',
        booking_status: 'not_booked',
        nightly_price_cny: null,
      }
      const result = await selectTravelLodgingV2(trip.trip_id, {
        expected_revision: trip.revision, lodging,
      })
      onTripSaved?.({ ...trip, revision: result.revision, document: result.document }, '住宿参考已保存，尚未预订。')
      setSavedMessage('已选为住宿并保存，尚未预订。房价待核实。')
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : '保存失败，原行程保持不变。')
    } finally {
      setSaving(false)
    }
  }

  async function addTrain(train: TrainSearchResultV2) {
    if (!trip || saving || train.duration_min === null) return
    setSaving(true)
    setSaveError('')
    setSavedMessage('')
    try {
      const selectedTrain: IntercityTrainArrangementV2 = {
        selection_id: train.selection_id,
        train_code: train.train_code,
        travel_date: train.travel_date,
        departure_station: train.departure_station,
        arrival_station: train.arrival_station,
        departure_time: train.departure_time,
        arrival_time: train.arrival_time,
        duration_min: train.duration_min,
        tickets: train.tickets,
        fares: train.fares,
        ticket_source: train.ticket_source,
        fare_source: train.fare_source,
        queried_at: train.queried_at,
        verification: train.verification,
        booking_status: 'not_booked',
      }
      const result = await selectTravelTrainV2(trip.trip_id, {
        expected_revision: trip.revision, train: selectedTrain,
      })
      onTripSaved?.({ ...trip, revision: result.revision, document: result.document }, '城际交通安排已保存，尚未购票。')
      setSavedMessage('已加入行程并保存，尚未购票。车次信息可能延迟。')
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : '保存失败，原行程保持不变。')
    } finally {
      setSaving(false)
    }
  }

  return <section className="min-w-0 rounded-[20px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]">
    {showTabs && <nav aria-label="行程关联查询" className="flex gap-1 overflow-x-auto border-b border-[#edf1f6] px-3 pt-2.5">{[{ kind: 'trip' as const, label: '行程' }, ...searchOptions.map(({ kind: value, label }) => ({ kind: value, label }))].map(({ kind: value, label }) => <button key={value} type="button" aria-pressed={value === 'trip' ? false : kind === value} disabled={value === 'trip'} onClick={() => value !== 'trip' && setKind(value)} className={`shrink-0 border-b-2 px-3 py-2 text-[11px] font-medium ${value === kind ? 'border-[#2878f5] text-[#2878f5]' : value === 'trip' ? 'border-transparent text-[#a0aab7]' : 'border-transparent text-[#71839b] hover:text-[#2878f5]'}`}>{label}</button>)}</nav>}
    <div className="p-3 sm:p-4">
      <div className="mb-3 flex items-center justify-between gap-2"><div><h2 className="text-sm font-bold text-[#263b55]">{kind === 'food' ? '查询美食' : kind === 'hotel' ? '查询酒店' : '查询动车 / 高铁'}</h2><p className="mt-1 text-[9px] leading-4 text-[#8998aa]">只读查询 · 只有明确选择后才会保存到行程</p></div>{trip && <span className="shrink-0 rounded-full bg-[#f2f7fd] px-2.5 py-1 text-[9px] text-[#6d829b]">行程 v{trip.revision}</span>}</div>
      <form onSubmit={submitSearch} className="space-y-2.5">
        {kind !== 'train' ? <label className="block"><span className="sr-only">城市</span><input required value={city} onChange={(event) => setCity(event.target.value)} placeholder="输入城市，如杭州" className="h-10 w-full rounded-xl border border-[#dfe8f3] bg-[#fbfdff] px-3 text-xs text-[#344b67] outline-none focus:border-[#8db6f8]" /></label> : <div className="grid grid-cols-2 gap-2"><label><span className="sr-only">出发站</span><input required value={fromStation} onChange={(event) => setFromStation(event.target.value)} placeholder="出发站" className="h-10 w-full rounded-xl border border-[#dfe8f3] bg-[#fbfdff] px-3 text-xs text-[#344b67] outline-none focus:border-[#8db6f8]" /></label><label><span className="sr-only">到达站</span><input required value={toStation} onChange={(event) => setToStation(event.target.value)} placeholder="到达站" className="h-10 w-full rounded-xl border border-[#dfe8f3] bg-[#fbfdff] px-3 text-xs text-[#344b67] outline-none focus:border-[#8db6f8]" /></label><label className="col-span-2"><span className="sr-only">出发日期</span><input required type="date" value={travelDate} onChange={(event) => setTravelDate(event.target.value)} className="h-10 w-full rounded-xl border border-[#dfe8f3] bg-[#fbfdff] px-3 text-xs text-[#344b67] outline-none focus:border-[#8db6f8]" /></label></div>}
        <button type="submit" disabled={querying || (kind === 'train' && (!fromStation.trim() || !toStation.trim() || !travelDate)) || (kind !== 'train' && !city.trim())} className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#2878f5] text-xs font-semibold text-white disabled:cursor-wait disabled:bg-[#9fbae0]">{querying ? <><LoaderCircle size={14} className="animate-spin" aria-hidden />查询中…</> : <><Search size={14} aria-hidden />查询</>}</button>
      </form>

      {querying && <p role="status" className="mt-3 flex items-center gap-2 rounded-lg bg-[#f0f6ff] px-3 py-2.5 text-[10px] text-[#4a76ac]"><LoaderCircle size={13} className="animate-spin" aria-hidden />正在调用{kind === 'train' ? '12306 车次查询' : '高德商户查询'}…</p>}
      {requestError && <p role="alert" className="mt-3 flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2.5 text-[10px] leading-4 text-rose-700"><CircleAlert size={13} className="mt-0.5 shrink-0" aria-hidden />{requestError}</p>}
      {response && <div className="mt-3 space-y-3">
        <div role={response.status === 'success' || response.status === 'no_results' ? 'status' : 'alert'} className={`flex items-start justify-between gap-3 rounded-xl px-3 py-2.5 ${response.status === 'success' ? 'bg-emerald-50 text-emerald-800' : response.status === 'no_results' ? 'bg-[#f4f7fb] text-[#637891]' : 'bg-amber-50 text-amber-900'}`}><div><p className="text-[10px] font-semibold">{statusText[response.status] || '查询完成'}</p><p className="mt-0.5 text-[9px] leading-4">{response.message}</p></div><span className="shrink-0 text-[8px]">{sourceName(response.source)} · {formatTime(response.queried_at)}</span></div>
        {response.disclosure && <p className="rounded-lg bg-[#f7faff] px-3 py-2 text-[9px] leading-4 text-[#7f91a7]">{response.disclosure}</p>}
        {selected && <div className="rounded-[16px] border border-[#dce8f5] bg-[#fbfdff] p-3">
          <button type="button" onClick={() => { setSelectedIndex(null); setSavedMessage(''); setSaveError(''); onLocationSelect?.(null) }} className="mb-2 inline-flex items-center gap-1 text-[10px] font-medium text-[#6b819a]"><ArrowLeft size={12} aria-hidden />返回结果列表</button>
          {kind === 'train' ? <TrainDetail train={selected as TrainSearchResultV2} /> : <MerchantDetail merchant={selected as MerchantSearchResultV2} />}
          {trip && kind === 'food' && <div className="mt-3 space-y-2 border-t border-[#e9eff6] pt-3"><div className="grid grid-cols-2 gap-2"><label className="text-[9px] text-[#8191a6]">加入日期<select value={dayId} onChange={(event) => setDayId(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-[#e0e9f3] bg-white px-2 text-[10px] text-[#4b6380]">{trip.document.days.map((day, index) => <option key={day.day_id} value={day.day_id}>第 {index + 1} 天 · {day.date ?? '日期待定'}</option>)}</select></label><label className="text-[9px] text-[#8191a6]">用餐时段<select value={mealPeriod} onChange={(event) => setMealPeriod(event.target.value as 'lunch' | 'dinner')} className="mt-1 h-9 w-full rounded-lg border border-[#e0e9f3] bg-white px-2 text-[10px] text-[#4b6380]"><option value="lunch">午餐</option><option value="dinner">晚餐</option></select></label></div><button type="button" disabled={saving || !dayId} onClick={() => void addMeal(selected as MerchantSearchResultV2)} className="h-10 w-full rounded-xl bg-[#2878f5] text-[11px] font-semibold text-white disabled:opacity-60">{saving ? '正在保存…' : `加入第 ${trip.document.days.findIndex((item) => item.day_id === dayId) + 1} 天 · ${mealPeriod === 'lunch' ? '午餐' : '晚餐'}`}</button></div>}
          {trip && kind === 'hotel' && <div className="mt-3 space-y-2 border-t border-[#e9eff6] pt-3"><div className="grid grid-cols-2 gap-2"><label className="text-[9px] text-[#8191a6]">入住日期<input required type="date" value={checkIn} onChange={(event) => setCheckIn(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-[#e0e9f3] bg-white px-2 text-[10px] text-[#4b6380]" /></label><label className="text-[9px] text-[#8191a6]">退房日期<input required type="date" value={checkOut} min={checkIn} onChange={(event) => setCheckOut(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-[#e0e9f3] bg-white px-2 text-[10px] text-[#4b6380]" /></label></div><p className="text-[9px] text-amber-700">计划参考，未预订；房态与每晚房价待核实。</p><button type="button" disabled={saving || !checkIn || !checkOut || checkOut <= checkIn} onClick={() => void selectLodging(selected as MerchantSearchResultV2)} className="h-10 w-full rounded-xl bg-[#2878f5] text-[11px] font-semibold text-white disabled:opacity-60">{saving ? '正在保存…' : '选为本次行程住宿'}</button></div>}
          {trip && kind === 'train' && <div className="mt-3 border-t border-[#e9eff6] pt-3"><p className="mb-2 text-[9px] text-amber-800">计划参考，未购票；车次信息可能延迟。</p><button type="button" disabled={saving || (selected as TrainSearchResultV2).duration_min === null} onClick={() => void addTrain(selected as TrainSearchResultV2)} className="h-10 w-full rounded-xl bg-[#2878f5] text-[11px] font-semibold text-white disabled:opacity-60">{saving ? '正在保存…' : '使用该车次'}</button></div>}
          {saveError && <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-3 py-2 text-[10px] leading-4 text-rose-700">保存失败，原行程保持不变。{saveError}</p>}
          {savedMessage && <p role="status" className="mt-2 flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-[10px] text-emerald-800"><Check size={12} aria-hidden />{savedMessage}</p>}
        </div>}
        {!selected && response.results.length > 0 && <div className="max-h-[520px] space-y-2 overflow-y-auto">{response.results.map((item, index) => kind === 'train' ? <TrainRow key={(item as TrainSearchResultV2).selection_id} train={item as TrainSearchResultV2} onClick={() => openResult(index)} /> : <MerchantRow key={(item as MerchantSearchResultV2).selection_id} merchant={item as MerchantSearchResultV2} hotel={kind === 'hotel'} onClick={() => openResult(index)} />)}</div>}
        {!selected && response.status === 'no_results' && <div className="rounded-xl border border-dashed border-[#d6e1ed] p-5 text-center text-[10px] text-[#8494a7]">放宽关键词或调整出发日期后再查。</div>}
      </div>}
    </div>
  </section>
}

function MerchantRow({ merchant, hotel, onClick }: { merchant: MerchantSearchResultV2; hotel: boolean; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="flex w-full items-center gap-3 rounded-[15px] border border-[#e8eef5] bg-white p-3 text-left transition hover:border-[#bfd7f6]"><span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${hotel ? 'bg-[#eaf2ff] text-[#2878f5]' : 'bg-[#fff4e8] text-[#e18c32]'}`}>{hotel ? <Hotel size={19} aria-hidden /> : <Utensils size={18} aria-hidden />}</span><span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-semibold text-[#2d425e]">{merchant.name}</span><span className="mt-1 flex items-center gap-1 text-[9px] text-[#74879e]"><MapPin size={10} aria-hidden />{merchant.address || '地址待核实'}</span><span className="mt-1 block text-[9px] text-[#8091a7]">评分 {merchant.rating ?? '暂无'} · {hotel ? '房价待核实' : merchant.open_status || '营业状态未知'}</span></span><span className="shrink-0 text-[9px] font-medium text-[#2878f5]">查看</span></button>
}

function MerchantDetail({ merchant }: { merchant: MerchantSearchResultV2 }) {
  return <div><h3 className="text-sm font-bold text-[#2d425e]">{merchant.name}</h3><div className="mt-2 space-y-2 text-[10px] text-[#72849a]"><p className="flex items-start gap-1.5"><MapPin size={12} className="mt-0.5 shrink-0 text-[#2878f5]" aria-hidden />{merchant.address || '暂无地址信息'}</p><p>评分：{merchant.rating ?? '暂无评分'}</p>{merchant.open_status && <p>营业状态：{merchant.open_status}{merchant.open_time_today ? ` · ${merchant.open_time_today}` : ''}</p>}{merchant.avg_cost_cny !== null && <p>人均：¥{merchant.avg_cost_cny}</p>}<p>位置：{merchant.lat !== null && merchant.lng !== null ? `${merchant.lat.toFixed(5)}, ${merchant.lng.toFixed(5)}` : '坐标待核实'}</p><p>来源：{sourceName(merchant.source)} · {formatTime(merchant.queried_at)}</p></div>{merchant.nightly_price_cny === null && <p className="mt-3 rounded-lg bg-amber-50 px-2.5 py-2 text-[9px] text-amber-800">房态待核实 · 每晚房价待核实</p>}</div>
}

function TrainRow({ train, onClick }: { train: TrainSearchResultV2; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="w-full rounded-[15px] border border-[#e8eef5] bg-white p-3 text-left transition hover:border-[#bfd7f6]"><div className="flex items-center justify-between"><span className="text-[11px] font-bold text-[#2d425e]">{train.train_code}</span><span className="text-[9px] text-[#8292a6]">{train.travel_date}</span></div><div className="mt-2 flex items-center justify-between gap-2"><span className="text-xs font-semibold text-[#344b67]">{train.departure_time} <span className="mx-1 text-[#a0acba]">→</span> {train.arrival_time}</span><span className="text-[9px] text-[#75879d]">{train.duration || (train.duration_min ? `${train.duration_min} 分钟` : '历时待核实')}</span></div><div className="mt-1 text-[9px] text-[#74869b]">{train.departure_station} → {train.arrival_station}</div><div className="mt-2 flex justify-between gap-2 text-[9px] text-[#788aa0]"><span>{seatSummary(train.tickets)}</span><span className="text-[#d7653b]">{fareSummary(train.fares)}</span></div></button>
}

function TrainDetail({ train }: { train: TrainSearchResultV2 }) {
  return <div><div className="flex items-center justify-between"><h3 className="text-sm font-bold text-[#2d425e]">{train.train_code}</h3><span className="rounded-full bg-amber-50 px-2 py-1 text-[8px] text-amber-800">12306 聚合源 · 可能延迟</span></div><div className="mt-3 grid grid-cols-2 gap-3 rounded-xl bg-[#f6f9fd] p-3 text-[10px]"><div><p className="text-[#8a99aa]">出发</p><p className="mt-1 font-semibold text-[#344b67]">{train.departure_time} · {train.departure_station}</p></div><div><p className="text-[#8a99aa]">到达</p><p className="mt-1 font-semibold text-[#344b67]">{train.arrival_time} · {train.arrival_station}</p></div><div><p className="text-[#8a99aa]">日期 / 历时</p><p className="mt-1 font-semibold text-[#344b67]">{train.travel_date} · {train.duration || '待核实'}</p></div><div><p className="text-[#8a99aa]">余票</p><p className="mt-1 font-semibold text-[#344b67]">{seatSummary(train.tickets)}</p></div></div><p className="mt-2 text-[10px] text-[#72849a]">票价：{fareSummary(train.fares)}</p><p className="mt-2 text-[9px] text-[#90a0b2]">数据来源：{sourceName(train.ticket_source)} · 查询时间：{formatTime(train.queried_at)}</p></div>
}

function seatSummary(tickets: Record<string, string | number>): string {
  const entries = Object.entries(tickets)
  return entries.length ? entries.slice(0, 3).map(([name, value]) => `${name} ${value}`).join(' · ') : '余票待核实'
}

function fareSummary(fares: Record<string, number | string>): string {
  const entries = Object.entries(fares)
  return entries.length ? entries.slice(0, 2).map(([name, value]) => `${name} ¥${value}`).join(' · ') : '票价待核实'
}

function sourceName(source: string): string {
  return source === '12306' ? '12306 非官方聚合源' : source === 'amap' ? '高德' : source
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function tripEndDate(trip?: TravelTripV2): string {
  const start = trip?.document.brief.start_date
  const days = trip?.document.brief.day_count
  if (!start || !days) return ''
  const [year, month, day] = start.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day + Math.max(1, days - 1)))
  return date.toISOString().slice(0, 10)
}
