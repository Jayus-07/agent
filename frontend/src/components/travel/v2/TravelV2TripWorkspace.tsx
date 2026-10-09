'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ApiError } from '@/api/client'
import { ArrowLeft, CalendarDays, Check, ChevronDown, Clock3, MapPin, MoreHorizontal, RotateCcw, Search, Sparkles, Users, Wallet } from 'lucide-react'
import {
  applyTravelTripEditV2,
  fetchTravelTripRevisionsV2,
  fetchTravelTripV2,
  restoreTravelTripV2,
  type TravelTripV2,
  type TripDayV2,
  type TripEditOperationV2,
  type TripItemV2,
} from '@/api/travelV2'
import TravelSearchActivity from './TravelSearchActivity'
import TravelSearchPanel, { type TravelSearchEvent, type TravelSearchKind } from './TravelSearchPanel'
import TravelV2Map from './TravelV2Map'
import TravelV2EditDialog, { type TravelV2EditPanel } from './TravelV2EditDialog'
import { TravelBottomNav } from './TravelV2Frame'

type Tab = 'trip' | 'food' | 'hotel' | 'train'
const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'trip', label: '行程' }, { id: 'food', label: '美食' }, { id: 'hotel', label: '酒店' }, { id: 'train', label: '车次' },
]

export default function TravelV2TripWorkspace({ tripId, editMode = false }: { tripId: string; editMode?: boolean }) {
  const router = useRouter()
  const [trip, setTrip] = useState<TravelTripV2 | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<Tab>('trip')
  const [dayIndex, setDayIndex] = useState(0)
  const [selectedItemId, setSelectedItemId] = useState('')
  const [selectedLocation, setSelectedLocation] = useState<{ name: string; lat: number; lng: number } | null>(null)
  const [undoRevision, setUndoRevision] = useState<number | null>(null)
  const [undoing, setUndoing] = useState(false)
  const [savingMessage, setSavingMessage] = useState('')
  const [editPanel, setEditPanel] = useState<TravelV2EditPanel>(null)
  const [editError, setEditError] = useState('')
  const [editing, setEditing] = useState(false)
  const editingRef = useRef(false)
  const [leftPrompt, setLeftPrompt] = useState('')
  const [searchEvents, setSearchEvents] = useState<TravelSearchEvent[]>([])

  const recordSearchEvent = useCallback((event: TravelSearchEvent) => {
    setSearchEvents((current) => [event, ...current].slice(0, 5))
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void fetchTravelTripV2(tripId).then(async (result) => {
      let previousRevision: number | null = null
      try {
        const history = await fetchTravelTripRevisionsV2(tripId)
        const latest = history.revisions.find((entry) => entry.revision === result.revision)
        if (latest?.change_type !== 'restore_revision') previousRevision = latest?.parent_revision ?? null
      } catch {
        // 历史读取失败不影响正式行程加载，只隐藏撤销入口。
      }
      if (!cancelled) {
        setTrip(result)
        setUndoRevision(previousRevision)
      }
    }).catch((cause) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : '行程暂时无法加载。')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tripId])

  useEffect(() => {
    if (editPanel) setEditError('')
  }, [editPanel])

  const days = trip?.document.days ?? []
  const day = days[dayIndex] ?? null
  const selectedItem = day?.items.find((item) => item.item_id === selectedItemId) ?? day?.items[0] ?? null
  const allMapItemIds = useMemo(() => new Set((day?.items ?? []).map((item) => item.item_id)), [day])

  function selectDay(index: number) {
    const nextDay = days[index]
    setDayIndex(index)
    setSelectedItemId(nextDay?.items[0]?.item_id ?? '')
    setSelectedLocation(null)
  }

  function saved(nextTrip: TravelTripV2, summary: string) {
    setUndoRevision(trip?.revision ?? null)
    setTrip(nextTrip)
    setSavingMessage(`${summary} 已自动保存。`)
    setError('')
  }

  async function applyEdit(operation: TripEditOperationV2, summary: string): Promise<boolean> {
    if (!trip || editingRef.current) return false
    editingRef.current = true
    setEditing(true)
    setEditError('')
    setSavingMessage('')
    const previousItemIds = new Set(trip.document.days.flatMap((item) => item.items.map((entry) => entry.item_id)))
    try {
      const result = await applyTravelTripEditV2(trip.trip_id, {
        expected_revision: trip.revision,
        change_summary: summary,
        operation,
      })
      const nextTrip = { ...trip, revision: result.revision, document: result.document }
      setUndoRevision(trip.revision)
      setTrip(nextTrip)
      const budgetWarning = result.document.health.issues.find((issue) => issue.code.startsWith('BUDGET_'))
      setSavingMessage(`${summary}，已自动保存。${budgetWarning ? ` ${budgetWarning.message}` : ''}`)
      setSelectedLocation(null)
      const selectedExists = nextTrip.document.days.some((entry) => entry.items.some((stop) => stop.item_id === selectedItemId))
      const addedItem = nextTrip.document.days.flatMap((entry) => entry.items.map((stop) => ({ dayId: entry.day_id, stop }))).find(({ stop }) => !previousItemIds.has(stop.item_id))
      if (operation.op === 'add_day') {
        setDayIndex(nextTrip.document.days.length - 1)
        setSelectedItemId('')
      } else if (addedItem) {
        const targetIndex = nextTrip.document.days.findIndex((entry) => entry.day_id === addedItem.dayId)
        setDayIndex(Math.max(0, targetIndex))
        setSelectedItemId(addedItem.stop.item_id)
      } else if (selectedExists) {
        const selectedDayIndex = nextTrip.document.days.findIndex((entry) => entry.items.some((stop) => stop.item_id === selectedItemId))
        if (selectedDayIndex >= 0) setDayIndex(selectedDayIndex)
      } else {
        const safeDayIndex = Math.min(dayIndex, Math.max(0, nextTrip.document.days.length - 1))
        setDayIndex(safeDayIndex)
        setSelectedItemId(nextTrip.document.days[safeDayIndex]?.items[0]?.item_id ?? '')
      }
      setEditPanel(null)
      return true
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '保存失败，正式行程没有显示为已保存。'
      if (cause instanceof ApiError && cause.status === 409 && cause.code === 'VERSION_CONFLICT') {
        try {
          const latest = await fetchTravelTripV2(trip.trip_id)
          setTrip(latest)
          setUndoRevision(null)
          setEditError('行程版本已变化；已载入服务器最新版本，本次操作未应用。请检查后重试。')
        } catch {
          setEditError(message)
        }
      } else {
        setEditError(message)
      }
      return false
    } finally {
      editingRef.current = false
      setEditing(false)
    }
  }

  async function undo() {
    if (!trip || undoRevision === null || undoing) return
    setUndoing(true)
    setError('')
    try {
      const result = await restoreTravelTripV2(trip.trip_id, {
        expected_revision: trip.revision,
        target_revision: undoRevision,
      })
      setTrip({ ...trip, revision: result.revision, document: result.document })
      setUndoRevision(null)
      setSavingMessage('已撤销上一次保存。')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '撤销失败，请刷新行程后重试。')
    } finally {
      setUndoing(false)
    }
  }

  function selectSearch(tab: Exclude<Tab, 'trip'>) {
    setActiveTab(tab)
    setSelectedLocation(null)
    setSavingMessage('')
  }

  const onSelectMapItem = useCallback((id: string) => {
    if (allMapItemIds.has(id)) setSelectedItemId(id)
  }, [allMapItemIds])

  const editItem = editPanel?.kind === 'item'
    ? days.flatMap((entry) => entry.items).find((entry) => entry.item_id === editPanel.itemId) ?? null
    : null
  const editRoute = editPanel?.kind === 'transport'
    ? { fromItemId: editPanel.fromItemId, toItemId: editPanel.toItemId }
    : undefined

  if (loading) return <main className="flex min-h-screen items-center justify-center bg-[#f4f8fd] text-sm text-[#70839b]">正在读取 V2 行程…</main>
  if (!trip) return <main className="min-h-screen bg-[#f4f8fd] p-5 text-[#17283f]"><a href="/travel/itineraries" className="text-sm text-[#2878f5]">返回我的行程</a><p role="alert" className="mx-auto mt-8 max-w-lg rounded-xl bg-white p-5 text-sm text-rose-700">{error || '没有找到这条 V2 行程。'}</p></main>

  const brief = trip.document.brief
  const budget = brief.budget.amount === null ? '待设置' : `¥${brief.budget.amount.toLocaleString()}`

  return <main className="flex min-h-screen flex-col bg-[#f4f8fd] text-[#17283f] lg:h-screen lg:min-h-0">
    <header className="hidden h-[68px] shrink-0 items-center justify-between gap-4 border-b border-[#e6edf5] bg-white px-5 lg:flex xl:px-7">
      <div className="flex min-w-0 items-center gap-3"><a href="/travel/itineraries" aria-label="返回行程列表" className="flex h-9 w-9 items-center justify-center rounded-full text-[#6b7f98] hover:bg-[#f3f7fc]"><ArrowLeft size={17} aria-hidden /></a><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><MapPin size={17} aria-hidden /></span><div className="min-w-0"><h1 className="truncate text-sm font-bold text-[#233a56]">{trip.title || brief.destination} · {brief.day_count} 天行程</h1><p className="mt-1 truncate text-[9px] text-[#8493a6]">{brief.start_date || '日期待定'} · {brief.travelers.adults + brief.travelers.children} 人 · 预算 {budget}</p></div></div>
      <div className="flex shrink-0 items-center gap-2"><span className="rounded-full bg-[#f1f6fc] px-3 py-1.5 text-[9px] font-medium text-[#6380a0]">V2 正式行程 · v{trip.revision}</span>{undoRevision !== null && <button type="button" onClick={() => void undo()} disabled={undoing} className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#dce6f1] bg-white px-3 text-[10px] font-medium text-[#5b718d] disabled:opacity-50"><RotateCcw size={13} aria-hidden />{undoing ? '撤销中…' : '撤销'}</button>}<button type="button" onClick={() => setEditPanel(selectedItem ? { kind: 'item', itemId: selectedItem.item_id } : { kind: 'add' })} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3.5 text-[10px] font-semibold text-white"><MoreHorizontal size={14} aria-hidden />{editMode ? '编辑模式' : '编辑行程'}</button></div>
    </header>

    <header className="sticky top-0 z-20 flex h-[56px] shrink-0 items-center justify-between border-b border-[#e5ecf4] bg-white px-3 lg:hidden"><a href="/travel/itineraries" aria-label="返回行程列表" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984]"><ArrowLeft size={18} aria-hidden /></a><div className="min-w-0 px-2 text-center"><h1 className="truncate text-sm font-semibold text-[#263b55]">{trip.title || brief.destination}</h1><p className="mt-0.5 text-[9px] text-[#92a0b1]">{brief.day_count} 天 · {brief.start_date || '日期待定'} · v{trip.revision}</p></div><button type="button" onClick={() => setEditPanel(selectedItem ? { kind: 'item', itemId: selectedItem.item_id } : { kind: 'add' })} className="rounded-full bg-[#2878f5] px-3 py-2 text-[10px] font-semibold text-white">编辑</button></header>

    <div className="hidden min-h-0 flex-1 gap-3 p-3 lg:grid lg:grid-cols-[280px_minmax(430px,.95fr)_minmax(390px,1.05fr)] xl:grid-cols-[300px_minmax(470px,.98fr)_minmax(420px,1.02fr)]">
      <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]"><header className="border-b border-[#edf1f6] px-4 py-3"><div className="flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={15} aria-hidden /></span><div><h2 className="text-sm font-bold text-[#263b55]">AI 旅行助手</h2><p className="mt-0.5 text-[9px] text-[#8a98aa]">酒店、美食和车次实时查询</p></div></div></header><div className="min-h-0 flex-1 overflow-y-auto p-3"><div className="rounded-xl bg-[#f2f7fd] px-3 py-3 text-[10px] leading-5 text-[#49617e]">行程：{brief.origin ? `${brief.origin} 出发，` : ''}{brief.destination}，{brief.day_count} 天。你可以查看真实商户与车次结果，再明确选择加入正式行程。</div><p className="mb-2 mt-4 text-[10px] font-bold text-[#405874]">快捷查询</p><div className="grid grid-cols-3 gap-1.5">{([['hotel','酒店'],['food','美食'],['train','动车']] as const).map(([tab,label]) => <button key={tab} type="button" onClick={() => selectSearch(tab)} className="rounded-lg border border-[#e4ebf3] bg-white px-1 py-2.5 text-[9px] font-medium text-[#5e748f] hover:border-[#b9d3fa] hover:text-[#2878f5]">{label}</button>)}</div><div className="mt-4"><p className="mb-2 text-[9px] font-semibold text-[#536b86]">查询过程 · Tool 状态</p><TravelSearchActivity events={searchEvents}/></div><div className="mt-4 rounded-xl border border-[#e8eef5] p-3"><p className="text-[9px] font-semibold text-[#536b86]">已选参考</p><p className="mt-2 text-[9px] text-[#8292a6]">住宿 {trip.document.arrangements.lodgings.length} 家 · 城际车次 {trip.document.arrangements.intercity_trains.length} 条 · 餐饮 {trip.document.days.flatMap((item) => item.items).filter((item) => item.activity_type === 'meal').length} 次</p><p className="mt-2 text-[8px] leading-4 text-[#a0aaba]">住宿和车次仅为计划参考，尚未完成预订或购票。</p></div><form onSubmit={(event) => { event.preventDefault(); if (leftPrompt.trim()) { setSavingMessage('V2 行程对话修改接口尚未接入；此输入不会修改行程。'); setLeftPrompt('') } }} className="mt-4"><label htmlFor="v2-trip-assistant" className="sr-only">向 AI 提问</label><div className="flex items-end gap-1 rounded-xl border border-[#dfe8f3] bg-[#fbfdff] p-1.5"><textarea id="v2-trip-assistant" rows={2} value={leftPrompt} onChange={(event) => setLeftPrompt(event.target.value)} placeholder="问景点、天气或行程…" className="min-h-10 flex-1 resize-none bg-transparent px-1.5 py-1 text-[9px] leading-4 text-[#344b67] outline-none placeholder:text-[#9aa8b8]" /><button type="submit" aria-label="发送问题" className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#2878f5] text-white"><Search size={13} aria-hidden /></button></div><p className="mt-1.5 px-1 text-[8px] text-[#9aa8b8]">行程对话修改尚未接入 V2；输入不会保存或修改行程。</p></form>{savingMessage && <p role="status" className="mt-3 rounded-lg bg-amber-50 px-2.5 py-2 text-[9px] leading-4 text-amber-800">{savingMessage}</p>}</div></aside>

      <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]"><nav aria-label="行程与查询" className="flex shrink-0 gap-1 overflow-x-auto border-b border-[#edf1f6] px-3 pt-2.5">{TABS.map((tab) => <button key={tab.id} type="button" aria-pressed={activeTab === tab.id} onClick={() => { setActiveTab(tab.id); setSavingMessage('') }} className={`shrink-0 border-b-2 px-3 py-2 text-[11px] font-medium ${activeTab === tab.id ? 'border-[#2878f5] text-[#2878f5]' : 'border-transparent text-[#71839b] hover:text-[#2878f5]'}`}>{tab.label}</button>)}</nav>{activeTab === 'trip' ? <div className="flex min-h-0 flex-1 flex-col"><header className="shrink-0 border-b border-[#edf1f6] px-4 pb-3 pt-3.5"><div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-bold text-[#263b55]">{brief.destination} 行程时间轴</h2><p className="mt-1 text-[9px] text-[#8a98aa]">按天安排 · 与地图地点双向联动</p></div><button type="button" onClick={() => selectedItem ? setEditPanel({ kind: 'item', itemId: selectedItem.item_id }) : setEditPanel({ kind: 'add' })} className={`rounded-lg px-2.5 py-2 text-[9px] font-medium bg-[#f4f7fb] text-[#73849a]`}>调整顺序</button></div><div className="flex gap-2 overflow-x-auto pb-1">{days.map((item, index) => <button key={item.day_id} type="button" aria-pressed={dayIndex === index} onClick={() => selectDay(index)} className={`min-w-[88px] rounded-xl border px-3 py-2 text-left ${dayIndex === index ? 'border-[#76aaf4] bg-[#f2f7ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#6e8097]'}`}><span className="block text-[10px] font-semibold">第 {index + 1} 天</span><span className="mt-1 block text-[8px]">{item.date || '日期待定'}</span></button>)}</div></header><div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{savingMessage && <div className="mb-3 flex items-center justify-between gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-[9px] text-emerald-800"><span className="flex items-center gap-1"><Check size={12} aria-hidden />{savingMessage}</span>{undoRevision !== null && <button type="button" onClick={() => void undo()} className="inline-flex shrink-0 items-center gap-1 font-medium"><RotateCcw size={11} aria-hidden />撤销</button>}</div>}{error && <p role="alert" className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[9px] text-rose-700">{error}</p>}<div className="mb-3 flex items-center justify-between"><div><p className="text-[11px] font-semibold text-[#344b67]">第 {dayIndex + 1} 天 · {day?.date || '日期待定'}</p><p className="mt-1 text-[9px] text-[#93a0b1]">{day?.items.length ?? 0} 个安排</p></div></div>{day?.items.length ? <div className="space-y-1.5">{day.items.map((item, index) => <div key={item.item_id}><button type="button" onClick={() => { setSelectedItemId(item.item_id); setSelectedLocation(null) }} className={`flex w-full items-center gap-2.5 rounded-[15px] border p-2.5 text-left ${selectedItem?.item_id === item.item_id ? 'border-[#78aaf4] shadow-[0_4px_14px_rgba(40,120,245,.1)]' : 'border-[#e8eef5]'}`}><span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white ${selectedItem?.item_id === item.item_id ? 'bg-[#175fc5]' : 'bg-[#2878f5]'}`}>{index + 1}</span><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><span className="truncate text-[11px] font-semibold text-[#2b405b]">{item.title}</span><span className="shrink-0 text-[9px] text-[#8190a3]">{item.start_time || '时间待定'}</span></span><span className="mt-1 flex items-center gap-2 text-[9px] text-[#8190a3]"><span className="inline-flex items-center gap-1"><Clock3 size={10} aria-hidden />停留 {item.duration_min} 分钟</span>{item.activity_type === 'meal' && <span className="rounded bg-[#fff5e9] px-1.5 py-0.5 text-[#b57835]">餐饮</span>}</span><span className="mt-1 block truncate text-[9px] text-[#98a5b4]">{item.place?.address || item.note || item.place?.facts.source || '地点详情'}</span></span><MoreHorizontal size={15} className="shrink-0 text-[#8798ad]" aria-hidden /></button>{index < day.items.length - 1 && <div className="ml-10 flex h-8 items-center gap-2"><span className="h-px w-4 border-t border-dashed border-[#bdd2ec]"/><button type="button" onClick={() => setEditPanel({ kind: 'transport', fromItemId: item.item_id, toItemId: day.items[index + 1].item_id })} className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[9px] text-[#72849b]">交通 · {legLabel(day, item, day.items[index + 1])} <ChevronDown size={10} aria-hidden /></button></div>}</div>)}</div> : <div className="rounded-xl border border-dashed border-[#dce6f2] bg-[#fbfdff] p-6 text-center text-xs text-[#8797aa]">这一天还没有安排。</div>}</div></div> : <div className="min-h-0 flex-1 overflow-y-auto p-3"><TravelSearchPanel key={`${activeTab}-${trip.trip_id}`} initialKind={activeTab as Exclude<TravelSearchKind, 'trip'>} trip={trip} showTabs={false} onTripSaved={saved} onLocationSelect={setSelectedLocation} onSearchEvent={recordSearchEvent}/></div>}</section>

      <aside className="flex min-h-0 min-w-0 flex-col gap-3 rounded-[22px] border border-[#e4ebf4] bg-white p-3 shadow-[0_6px_24px_rgba(31,74,130,.05)]"><div className="flex items-center justify-between px-1"><div><h2 className="text-sm font-bold text-[#263b55]">行程地图</h2><p className="mt-1 text-[9px] text-[#8a98aa]">点击编号定位行程地点</p></div><span className="rounded-full bg-[#f1f6fc] px-2.5 py-1 text-[9px] text-[#7589a1]">第 {dayIndex + 1} 天</span></div><div className="min-h-0 flex-1"><TravelV2Map day={day} selectedItemId={selectedItem?.item_id ?? ''} selectedLocation={selectedLocation} onSelectItem={onSelectMapItem}/></div><section className="flex min-h-[122px] gap-3 overflow-hidden rounded-[18px] border border-[#e8edf4] bg-white p-2.5"><span className="flex h-[92px] w-[102px] shrink-0 items-center justify-center rounded-xl bg-[#edf4fc] text-[#82a4cc]"><MapPin size={24} aria-hidden /></span><div className="min-w-0 flex-1 py-0.5"><p className="text-[8px] font-medium text-[#5b89c0]">{selectedLocation ? '查询结果位置' : '行程地点详情'}</p><h3 className="mt-1 truncate text-sm font-semibold text-[#2b405b]">{selectedLocation?.name || selectedItem?.title || '选择地点查看详情'}</h3><p className="mt-2 line-clamp-2 text-[9px] leading-4 text-[#75869a]">{selectedLocation ? '位置来自刚才的实时搜索结果。' : selectedItem?.place?.address || selectedItem?.note || '当前地点没有详细地址。'}</p><div className="mt-2 flex items-center gap-3 text-[9px] text-[#8292a6]">{selectedItem && <span className="inline-flex items-center gap-1"><Clock3 size={10} aria-hidden/>{selectedItem.start_time || '时间待定'}</span>}{selectedItem && <span>停留 {selectedItem.duration_min} 分钟</span>}</div></div></section><div className="flex items-center justify-between border-t border-[#edf1f6] px-1 pt-2 text-[8px] text-[#96a3b2]"><span>地图位置根据可用数据展示</span><span>V2 工作台</span></div></aside>
    </div>

    <div className="flex-1 pb-24 lg:hidden"><section className="mx-3 mt-3 rounded-[18px] border border-[#e4ebf4] bg-white p-3 shadow-[0_4px_16px_rgba(31,74,130,.04)]"><div className="flex items-start justify-between gap-3"><div><p className="text-[9px] font-medium text-[#5a87bd]">正式行程 · 自动保存</p><h2 className="mt-1 text-base font-bold text-[#263b55]">{trip.title || brief.destination}</h2><p className="mt-1 text-[10px] text-[#8796a8]">{brief.start_date || '日期待定'} · {brief.day_count} 天 · {brief.travelers.adults + brief.travelers.children} 人</p></div><span className="rounded-full bg-[#f1f6fc] px-2 py-1 text-[8px] text-[#6380a0]">v{trip.revision}</span></div><div className="mt-3 grid grid-cols-3 divide-x divide-[#e8edf4] rounded-xl bg-[#f7faff] py-2.5"><div className="text-center"><p className="text-[8px] text-[#91a0b1]">人数</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{brief.travelers.adults + brief.travelers.children} 位</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">预算</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{budget}</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">节奏</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{paceLabel(brief.pace)}</p></div></div></section>
      <nav aria-label="行程和查询切换" className="mx-3 mt-3 grid grid-cols-4 rounded-xl border border-[#e5ecf5] bg-white p-1">{TABS.map((tab) => <button key={tab.id} type="button" aria-pressed={activeTab === tab.id} onClick={() => { setActiveTab(tab.id); setSavingMessage('') }} className={`h-9 rounded-lg text-[10px] ${activeTab === tab.id ? 'bg-[#edf5ff] font-semibold text-[#2878f5]' : 'text-[#7e8da1]'}`}>{tab.label}</button>)}</nav>
      {activeTab === 'trip' ? <><div className="mx-3 mt-3 flex gap-2 overflow-x-auto pb-1">{days.map((item, index) => <button key={item.day_id} type="button" aria-pressed={dayIndex === index} onClick={() => selectDay(index)} className={`min-w-[92px] rounded-xl border px-3 py-2 text-center ${dayIndex === index ? 'border-[#79aaf2] bg-[#edf5ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#788ba3]'}`}><span className="block text-[10px] font-semibold">第 {index + 1} 天</span><span className="mt-1 block text-[8px]">{item.date || '日期待定'}</span></button>)}</div><div className="mx-3 mt-2 h-[230px]"><TravelV2Map day={day} selectedItemId={selectedItem?.item_id ?? ''} selectedLocation={selectedLocation} onSelectItem={onSelectMapItem}/></div><section className="mx-3 mt-3 rounded-[20px] border border-[#e4ebf4] bg-white p-3 shadow-[0_4px_16px_rgba(31,74,130,.04)]"><header className="mb-3 flex items-center justify-between"><div><h2 className="text-xs font-bold text-[#2d435e]">第 {dayIndex + 1} 天安排</h2><p className="mt-1 text-[9px] text-[#92a0b1]">{day?.items.length ?? 0} 个地点 · 点击卡片联动地图</p></div><button type="button" onClick={() => selectedItem ? setEditPanel({ kind: 'item', itemId: selectedItem.item_id }) : setEditPanel({ kind: 'add' })} className={`rounded-lg px-2.5 py-2 text-[9px] bg-[#f3f7fc] text-[#71839b]`}>调整顺序</button></header>{savingMessage && <div className="mb-3 flex items-center justify-between gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-[9px] text-emerald-800"><span className="flex items-center gap-1"><Check size={12} aria-hidden />{savingMessage}</span>{undoRevision !== null && <button type="button" onClick={() => void undo()} className="inline-flex items-center gap-1"><RotateCcw size={11} aria-hidden/>撤销</button>}</div>}{error && <p role="alert" className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[9px] text-rose-700">{error}</p>}{day?.items.length ? <div className="space-y-2">{day.items.map((item, index) => <article key={item.item_id} className={`rounded-[15px] border p-2.5 ${selectedItem?.item_id === item.item_id ? 'border-[#78aaf4] shadow-[0_4px_14px_rgba(40,120,245,.1)]' : 'border-[#e8eef5]'}`}><div className="flex items-center gap-2.5"><button type="button" onClick={() => { setSelectedItemId(item.item_id); setSelectedLocation(null) }} aria-label={`地图定位到第 ${index + 1} 站 ${item.title}`} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#2878f5] text-[10px] font-semibold text-white">{index + 1}</button><button type="button" onClick={() => { setSelectedItemId(item.item_id); setSelectedLocation(null) }} className="min-w-0 flex-1 text-left"><span className="flex items-center justify-between gap-2"><span className="truncate text-xs font-semibold text-[#2b405b]">{item.title}</span><span className="text-[9px] text-[#8190a3]">{item.start_time || '时间待定'}</span></span><span className="mt-1 block truncate text-[9px] text-[#98a5b4]">{item.place?.address || item.note || `停留 ${item.duration_min} 分钟`}</span></button><button type="button" onClick={() => setEditPanel({ kind: 'item', itemId: item.item_id })} aria-label={`编辑${item.title}`} className="rounded-lg p-1 text-[#8798ad]"><MoreHorizontal size={16} aria-hidden/></button></div>{index < day.items.length - 1 && <button type="button" onClick={() => setEditPanel({ kind: 'transport', fromItemId: item.item_id, toItemId: day.items[index + 1].item_id })} className="ml-10 mt-2 flex items-center gap-2 text-[9px] text-[#72849b]">交通 · {legLabel(day, item, day.items[index + 1])} <ChevronDown size={10} aria-hidden/></button>}</article>)}</div> : <div className="rounded-xl border border-dashed border-[#dce6f2] p-6 text-center text-xs text-[#8797aa]">这一天还没有安排。</div>}<button type="button" onClick={() => setEditPanel({ kind: 'add' })} className="mt-3 h-10 w-full rounded-xl border border-dashed border-[#a9c7ed] bg-[#f6faff] text-xs font-semibold text-[#2878f5]">＋ 添加地点或活动</button></section></> : <div className="mx-3 mt-3"><TravelSearchPanel key={`mobile-${activeTab}-${trip.trip_id}`} initialKind={activeTab as Exclude<TravelSearchKind, 'trip'>} trip={trip} showTabs={false} onTripSaved={saved} onLocationSelect={setSelectedLocation} onSearchEvent={recordSearchEvent}/></div>}
    </div>
    {editPanel && <TravelV2EditDialog
      panel={editPanel}
      trip={trip}
      dayId={day?.day_id ?? days[0]?.day_id ?? ''}
      item={editItem}
      route={editRoute}
      saving={editing}
      error={editError}
      onClose={() => setEditPanel(null)}
      onEdit={applyEdit}
    />}
    <TravelBottomNav active="trips" />
  </main>
}

function legLabel(day: TripDayV2, from: TripItemV2, to: TripItemV2): string {
  const leg = day.legs.find((item) => item.from_item_id === from.item_id && item.to_item_id === to.item_id)
  if (!leg) return '待选择'
  const labels = { walk: '步行', drive: '驾车', transit: '公共交通', taxi: '出租车' }
  return `${labels[leg.selected_mode]}${leg.duration_min ? ` · ${leg.duration_min} 分钟` : ''}`
}

function paceLabel(pace: string): string {
  return pace === 'relaxed' ? '轻松' : pace === 'intense' ? '紧凑' : '均衡'
}
