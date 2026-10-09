'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, ArrowRight, Clock3, MapPin, MoreHorizontal, Route, SlidersHorizontal, Sparkles } from 'lucide-react'
import { fetchTravelPlanLatest, type Itinerary, type ItineraryDay, type ItineraryItem, type TravelPlanLatest, type TransitLeg } from '@/api/travel'
import { TravelBottomNav } from './TravelV2Frame'
import TravelLiveAssistantPanel from './TravelLiveAssistantPanel'
import TravelLiveMap from './TravelLiveMap'
import { resolveTravelActiveItinerary } from './travelLiveRuntime'

export default function TravelLiveWorkspace({ conversationId, editMode = false }: { conversationId: string; editMode?: boolean }) {
  const router = useRouter()
  const [latest, setLatest] = useState<TravelPlanLatest | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [dayIndex, setDayIndex] = useState(0)
  const [selectedId, setSelectedId] = useState('')
  const [editMenuOpen, setEditMenuOpen] = useState(false)
  const [transportOpen, setTransportOpen] = useState(false)

  const reload = useCallback(() => setReloadKey((value) => value + 1), [])
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    void fetchTravelPlanLatest(conversationId).then((data) => {
      if (!cancelled) setLatest(data)
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : '行程暂时无法加载。')
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [conversationId, reloadKey])

  const itinerary = latest ? resolveTravelActiveItinerary(latest) : null
  const days = useMemo(() => (itinerary?.days ?? []).slice().sort((left, right) => left.day_index - right.day_index), [itinerary])
  const day = days[dayIndex] ?? days[0]
  const dayItems = day?.items ?? []
  const selectedItem = useMemo(() => {
    if (!day) return null
    const index = dayItems.findIndex((item, itemIndex) => `${day.day_index}:${itemIndex}:${item.title}` === selectedId)
    return index >= 0 ? { item: dayItems[index], index } : null
  }, [day, dayItems, selectedId])

  const selectItem = useCallback((id: string) => setSelectedId(id), [])
  const activeVersion = latest?.active_plan_version ?? latest?.plan_version
  const hasWaitingDraft = latest?.plan_status === 'waiting_confirmation'

  function requestEdit(action: string) {
    const title = selectedItem?.item.title
    if (!title) return
    const prompt = action.replace('{地点}', title).replace('{日期}', `第${day?.day_index ?? 1}天`)
    router.push(`/travel/chat?conversation_id=${encodeURIComponent(conversationId)}&prompt=${encodeURIComponent(prompt)}`)
  }

  return <main className="flex min-h-screen flex-col bg-[#f4f8fd] text-[#17283f] lg:h-screen lg:min-h-0">
    <header className="hidden h-[78px] shrink-0 items-center justify-between gap-4 border-b border-[#e6edf5] bg-white px-5 lg:flex xl:px-7"><div className="flex min-w-0 items-center gap-3"><a href="/travel/itineraries" aria-label="返回我的行程" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#6b7f98] hover:bg-[#f3f7fc]"><ArrowLeft size={17} aria-hidden /></a><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><MapPin size={17} aria-hidden /></span><div className="min-w-0"><h1 className="truncate text-base font-bold text-[#233a56]">{itinerary?.brief.destination || latest?.destination || '旅行行程'} · {days.length}天{Math.max(0, days.length - 1)}晚</h1><p className="mt-1 truncate text-[10px] text-[#8493a6]">{itinerary?.brief.start_date || '日期待定'} <span className="mx-1 text-[#d1dae5]">·</span>{itinerary?.brief.party_size ?? '人数待定'}人 <span className="mx-1 text-[#d1dae5]">·</span>{itinerary?.brief.budget_cny != null ? `预算 ¥${itinerary.brief.budget_cny}` : '预算待定'}</p></div></div><div className="flex shrink-0 items-center gap-2"><span className={`rounded-full px-3 py-1.5 text-[9px] font-medium ${hasWaitingDraft ? 'bg-amber-50 text-amber-800' : 'bg-[#f1f6fc] text-[#6380a0]'}`}>{hasWaitingDraft ? `当前显示已保存版本 v${activeVersion}` : latest?.plan_status === 'confirmed' ? '已保存到账号' : latest?.plan_status || '读取行程'}</span><button type="button" onClick={() => router.push(`/travel/itineraries/${encodeURIComponent(conversationId)}/edit`)} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3.5 text-[10px] font-semibold text-white"><SlidersHorizontal size={14} aria-hidden />编辑行程</button></div></header>
    <header className="sticky top-0 z-20 flex h-[56px] shrink-0 items-center justify-between border-b border-[#e5ecf4] bg-white px-3 lg:hidden"><a href="/travel/itineraries" aria-label="返回我的行程" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984]"><ArrowLeft size={18} aria-hidden /></a><div className="min-w-0 px-2 text-center"><h1 className="truncate text-sm font-semibold text-[#263b55]">{itinerary?.brief.destination || latest?.destination || '旅行行程'} · {days.length}天</h1><p className="mt-0.5 text-[9px] text-[#92a0b1]">{hasWaitingDraft ? `当前已保存 v${activeVersion}` : '行程详情'}</p></div><button type="button" onClick={() => router.push(`/travel/itineraries/${encodeURIComponent(conversationId)}/edit`)} className="rounded-full bg-[#2878f5] px-3 py-2 text-[10px] font-semibold text-white">编辑</button></header>

    {loading ? <div role="status" className="flex flex-1 items-center justify-center text-sm text-[#8191a6]">正在读取已保存行程…</div> : error ? <div className="mx-auto mt-12 max-w-xl rounded-2xl border border-amber-200 bg-amber-50 p-6 text-center"><p role="alert" className="text-sm font-semibold text-amber-900">行程读取失败</p><p className="mt-2 text-xs leading-5 text-amber-800">{error}</p><button type="button" onClick={reload} className="mt-4 rounded-lg bg-white px-4 py-2 text-xs font-semibold text-amber-900">重试</button></div> : !itinerary ? <div className="mx-auto mt-12 max-w-xl rounded-2xl border border-[#e4ebf4] bg-white p-8 text-center"><p className="text-sm font-semibold text-[#3c5471]">当前没有已确认行程</p><p className="mt-2 text-xs leading-5 text-[#8796a8]">服务端可能只保存了待确认修改，或当前账号无权查看此行程。</p><a href={`/travel/chat?conversation_id=${encodeURIComponent(conversationId)}`} className="mt-4 inline-flex rounded-lg bg-[#2878f5] px-4 py-2 text-xs font-semibold text-white">继续与 AI 对话</a></div> : <>
      {hasWaitingDraft && <div role="status" className="mx-3 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[10px] leading-4 text-amber-900 lg:mx-4">最新修改的服务端状态为待确认；此处展示当前已保存的 Active 行程，避免将草案误认为正式版本。</div>}
      {editMode && <div className="mx-3 mt-3 flex items-start gap-2 rounded-xl border border-[#d9e8ff] bg-[#f1f7ff] px-3 py-2.5 text-[10px] leading-4 text-[#49688e] lg:mx-4"><Sparkles size={13} className="mt-0.5 shrink-0 text-[#2878f5]" aria-hidden /><span>编辑模式：选择地点后可通过 AI 发起替换、时间、日期或交通修改。服务端返回新版本前，当前行程保持不变。</span></div>}
      <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[292px_minmax(425px,.92fr)_minmax(400px,1.08fr)] xl:grid-cols-[310px_minmax(440px,.92fr)_minmax(440px,1.08fr)]">
        <div className="order-3 min-h-[420px] lg:order-1 lg:min-h-0"><TravelLiveAssistantPanel conversationId={conversationId} onPlanUpdated={reload} /></div>
        <section className="order-1 flex min-h-[500px] min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)] lg:order-2 lg:min-h-0">
          <header className="shrink-0 border-b border-[#edf1f6] px-4 pb-3 pt-3.5"><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-bold text-[#263b55]">行程时间轴</h2><p className="mt-1 text-[9px] text-[#8a98aa]">服务端行程 · 地点和地图编号联动</p></div>{editMode && <button type="button" onClick={() => setEditMenuOpen((value) => !value)} aria-expanded={editMenuOpen} className="inline-flex items-center gap-1 rounded-lg bg-[#f4f7fb] px-2.5 py-2 text-[9px] font-medium text-[#73849a]"><MoreHorizontal size={14} aria-hidden />地点操作</button>}</div>
            <div className="flex gap-2 overflow-x-auto pb-1">{days.map((item, index) => <button key={item.day_index} type="button" onClick={() => { setDayIndex(index); setSelectedId('') }} aria-pressed={dayIndex === index} className={`min-w-[88px] rounded-xl border px-3 py-2 text-left ${dayIndex === index ? 'border-[#76aaf4] bg-[#f2f7ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#6e8097]'}`}><span className="block text-[10px] font-semibold">第 {item.day_index} 天</span><span className="mt-1 block text-[8px]">{item.day_date || '日期待定'}</span></button>)}</div>
            {editMenuOpen && selectedItem && <div className="mt-3 grid grid-cols-2 gap-1.5 rounded-xl border border-[#e5edf6] bg-[#f9fbfe] p-2">{[['替换地点', '请为{日期}的{地点}推荐一个替代地点。'], ['修改时间', '请调整{日期}{地点}的开始时间，并检查前后安排。'], ['移动日期', '请把{日期}的{地点}移到更合适的一天。'], ['修改交通', '请重新评估到{地点}的交通方式。']].map(([label, prompt]) => <button key={label} type="button" onClick={() => requestEdit(prompt)} className="rounded-lg bg-white px-2 py-2 text-[9px] font-medium text-[#54708f] hover:text-[#2878f5]">{label}</button>)}</div>}
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4 pt-3 sm:px-4"><div className="mb-3 flex items-center justify-between"><div><p className="text-[11px] font-semibold text-[#344b67]">第 {day?.day_index ?? 1} 天 · {day?.day_date || '日期待定'}</p><p className="mt-1 text-[9px] text-[#93a0b1]">{dayItems.length} 个安排 · {day?.active_minutes ?? 0} 分钟游览</p></div>{editMode && <span className="rounded-full bg-[#edf5ff] px-2.5 py-1 text-[8px] font-medium text-[#4d82bf]">编辑模式</span>}</div>
            {day && dayItems.length > 0 ? <div className="space-y-0">{dayItems.map((item, index) => <div key={`${day.day_index}:${index}:${item.title}`}><article className={`relative flex cursor-pointer gap-3 rounded-2xl border p-3 transition ${selectedId === `${day.day_index}:${index}:${item.title}` ? 'border-[#9fc4f8] bg-[#f7fbff]' : 'border-[#e8eef5] bg-white hover:border-[#cbdcf1]'}`} onClick={() => selectItem(`${day.day_index}:${index}:${item.title}`)} role="button" tabIndex={0} aria-pressed={selectedId === `${day.day_index}:${index}:${item.title}`} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') selectItem(`${day.day_index}:${index}:${item.title}`) }}><div className="flex w-9 shrink-0 flex-col items-center"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#2878f5] text-xs font-semibold text-white">{index + 1}</span>{index < dayItems.length - 1 && <span className="mt-2 min-h-5 flex-1 border-l border-dashed border-[#9fc2f2]" />}</div><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><span className="text-[9px] font-semibold text-[#678ab4]">{kindLabel(item.kind)}</span><h3 className="mt-0.5 truncate text-sm font-semibold text-[#243953]">{item.title}</h3></div><span className="shrink-0 text-[10px] font-medium text-[#7690ad]">{item.start || '时间待定'}</span></div>{item.note && <p className="mt-1.5 line-clamp-2 text-[10px] leading-5 text-[#7a8ba0]">{item.note}</p>}<div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-[#8798ab]"><span className="inline-flex items-center gap-1"><Clock3 size={11} aria-hidden />停留 {item.minutes || 0} 分钟</span>{item.poi && <span className="inline-flex items-center gap-1"><MapPin size={11} aria-hidden />{item.poi.location_status === 'verified' ? '坐标已核实' : '地点信息'}</span>}</div></div><span className="relative flex h-[70px] w-[78px] shrink-0 items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br from-[#e6f1ff] to-[#f6f9fd] sm:w-[100px]"><MapPin size={19} className="text-[#88a9d0]" aria-hidden /><span className="absolute bottom-1 text-[7px] text-[#8298b4]">暂无景点图片</span></span></article>
              {index < dayItems.length - 1 && <TransitBetween day={day} from={item} to={dayItems[index + 1]} onOpen={() => { setSelectedId(`${day.day_index}:${index + 1}:${dayItems[index + 1].title}`); setTransportOpen(true) }} />}
            </div>)}</div> : <p className="rounded-xl border border-dashed border-[#d3dfed] p-6 text-center text-xs text-[#8796a8]">这一天暂时没有安排。</p>}
          </div>
        </section>
        <aside className="order-2 flex min-h-[390px] min-w-0 flex-col gap-3 lg:order-3 lg:min-h-0 lg:rounded-[22px] lg:border lg:border-[#e4ebf4] lg:bg-white lg:p-3 lg:shadow-[0_6px_24px_rgba(31,74,130,.05)]"><div className="hidden items-center justify-between px-1 lg:flex"><div><h2 className="text-sm font-bold text-[#263b55]">路线地图</h2><p className="mt-1 text-[9px] text-[#8a98aa]">仅展示有已核实坐标的地点</p></div><span className="rounded-full bg-[#f1f6fc] px-2.5 py-1 text-[9px] text-[#7589a1]">第 {day?.day_index ?? 1} 天</span></div>{day && <TravelLiveMap day={day} selectedId={selectedId} onSelect={selectItem} />}
          {selectedItem ? <PlaceDetail item={selectedItem.item} day={day!} /> : <div className="hidden flex-1 items-center justify-center rounded-[18px] border border-[#e8edf4] text-xs text-[#91a0b2] lg:flex">选择地点查看详情</div>}
          {editMode && selectedItem && <div className="rounded-[18px] border border-[#e8edf4] bg-white p-3 lg:bg-[#f9fbfe]"><div className="mb-2 flex items-center justify-between"><p className="text-[10px] font-semibold text-[#485f7b]">编辑所选地点</p><button type="button" onClick={() => setEditMenuOpen((value) => !value)} className="text-[9px] text-[#2878f5]">更多操作</button></div><div className="grid grid-cols-2 gap-2"><button type="button" onClick={() => requestEdit('请为{地点}推荐替代地点。')} className="rounded-lg bg-[#edf5ff] px-2 py-2 text-[9px] text-[#2878f5]">替换景点</button><button type="button" onClick={() => setTransportOpen(true)} className="rounded-lg bg-[#f3f7fc] px-2 py-2 text-[9px] text-[#5c718b]">交通方式</button></div></div>}
        </aside>
      </div>
      <TravelBottomNav active="trips" />
      {transportOpen && <div className="fixed inset-0 z-50 flex items-end justify-center bg-[#17283f]/35 p-3 sm:items-center" role="presentation" onClick={() => setTransportOpen(false)}><section role="dialog" aria-modal="true" aria-label="交通方式" className="w-full max-w-sm rounded-[22px] bg-white p-4 shadow-2xl" onClick={(event) => event.stopPropagation()}><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-bold text-[#263b55]">调整交通方式</h2><p className="mt-1 text-[10px] text-[#8998aa]">将通过旅游 Agent 检查路线后修改</p></div><button type="button" onClick={() => setTransportOpen(false)} className="rounded-full bg-[#f3f7fc] px-3 py-1.5 text-[10px] text-[#6d8098]">关闭</button></div><div className="grid grid-cols-2 gap-2">{[['公共交通', '请重新规划到{地点}的公共交通方式，并检查耗时。'], ['步行', '请评估步行到{地点}是否合适。'], ['打车', '请评估到{地点}打车的路线和预算。'], ['自驾', '请评估到{地点}自驾是否可行。']].map(([label, prompt]) => <button key={label} type="button" onClick={() => requestEdit(prompt)} className="rounded-xl border border-[#e5edf6] bg-[#fbfdff] p-3 text-xs font-medium text-[#536b87]">{label}</button>)}</div></section></div>}
    </>}
  </main>
}

function kindLabel(kind: string): string {
  return ({ visit: '景点', meal: '餐饮', rest: '休息', activity: '活动', transit: '交通' } as Record<string, string>)[kind] || kind || '行程安排'
}

function TransitBetween({ day, from, to, onOpen }: { day: ItineraryDay; from: ItineraryItem; to: ItineraryItem; onOpen: () => void }) {
  const leg = day.legs?.find((item) => item.from_title === from.title && item.to_title === to.title)
  const isVerified = leg && !leg.is_estimate && /verified|amap|高德|地图/i.test(leg.source || '')
  return <button type="button" onClick={onOpen} className="ml-12 flex w-[calc(100%-3rem)] items-center gap-2 py-2 text-left"><span className="h-5 border-l border-dashed border-[#c2d6ef]" /><Route size={12} className="shrink-0 text-[#7da1cb]" aria-hidden /><span className="min-w-0 flex-1 truncate text-[9px] text-[#8596aa]">{leg ? `${transportLabel(leg.mode)} · ${leg.minutes} 分钟${isVerified ? '' : ' · 参考'}` : '交通信息未提供'}</span><span className="text-[8px] text-[#2878f5]">{leg ? '查看' : '设置'}</span></button>
}

function transportLabel(mode: string): string {
  return ({ walk: '步行', transit: '公共交通', driving: '驾车', taxi: '打车', bus: '公交', subway: '地铁' } as Record<string, string>)[mode] || mode || '交通'
}

function PlaceDetail({ item, day }: { item: ItineraryItem; day: ItineraryDay }) {
  return <section className="rounded-[18px] border border-[#e8edf4] bg-white p-3 lg:flex-1 lg:overflow-auto"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-[8px] font-medium text-[#5b89c0]">第 {day.day_index} 天 · {kindLabel(item.kind)}</p><h3 className="mt-1 truncate text-sm font-semibold text-[#2b405b]">{item.title}</h3></div><span className="rounded-md bg-[#edf5ff] px-2 py-1 text-[8px] text-[#5683b8]">{item.poi?.location_status === 'verified' ? '坐标已核实' : '信息待核实'}</span></div><p className="mt-2 text-[10px] leading-5 text-[#75869a]">{item.note || '暂无补充说明。'}</p><div className="mt-3 flex flex-wrap items-center gap-3 text-[9px] text-[#8292a6]"><span className="inline-flex items-center gap-1"><Clock3 size={11} aria-hidden />{item.start || '时间待定'}–{item.end || '时间待定'}</span><span>停留 {item.minutes || 0} 分钟</span>{item.poi && Number.isFinite(item.poi.lat) && Number.isFinite(item.poi.lng) && <span>{item.poi.lat.toFixed(4)}, {item.poi.lng.toFixed(4)}</span>}</div></section>
}
