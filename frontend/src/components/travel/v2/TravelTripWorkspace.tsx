'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, ArrowRight, CalendarDays, Check, ChevronDown, Clock3, MapPin, MoreHorizontal, Plus, RotateCcw, SlidersHorizontal, Sparkles, Users, Wallet } from 'lucide-react'
import { DemoTripProvider, useDemoTrip } from './DemoTripContext'
import { TRAVEL_CANDIDATES } from './travelV2Data'
import type { DemoTripStop, DemoTransportMode } from './travelV2State'
import { DemoNotice, TravelBottomNav } from './TravelV2Frame'
import TripChatPanel from './TripChatPanel'
import TripEditPanels, { type TripEditPanel } from './TripEditPanels'
import TravelRouteMap from './TravelRouteMap'
import TripTimeline from './TripTimeline'

export default function TravelTripWorkspace({ tripId = 'demo', editMode = false }: { tripId?: string; editMode?: boolean }) {
  return <WorkspaceContent tripId={tripId} editMode={editMode} />
}

function WorkspaceContent({ tripId, editMode }: { tripId: string; editMode: boolean }) {
  const router = useRouter()
  const { state, edit, undo } = useDemoTrip()
  const [dayIndex, setDayIndex] = useState(0)
  const [selectedStopId, setSelectedStopId] = useState('lingyin')
  const [sortMode, setSortMode] = useState(false)
  const [revealedStopId, setRevealedStopId] = useState<string | null>(null)
  const [panel, setPanel] = useState<TripEditPanel>(null)
  const [notice, setNotice] = useState('')
  const [mobileFocus, setMobileFocus] = useState<'all' | 'map' | 'timeline'>('all')
  const [editMenuOpen, setEditMenuOpen] = useState(false)
  const [chatDraft, setChatDraft] = useState('')
  const [chatSent, setChatSent] = useState<string[]>([])
  const days = state.itinerary.days
  const day = days[dayIndex] ?? days[0]
  const activeStop = day?.stops.find((stop) => stop.id === selectedStopId) ?? day?.stops[0]
  const panelKey = panel ? `${panel.kind}-${'stopId' in panel ? panel.stopId : 'stop' in panel ? panel.stop.id : 'replacementId' in panel ? panel.replacementId ?? 'new' : 'none'}` : 'closed'

  function applyEdit(action: Parameters<typeof edit>[0], message = '演示修改已应用到当前页面，没有保存到账号。') {
    edit(action)
    setNotice(message)
    setRevealedStopId(null)
  }

  function selectStop(stop: DemoTripStop, stopNumber: number) {
    setSelectedStopId(stop.id)
    setRevealedStopId(null)
    if (window.matchMedia('(max-width: 1023px)').matches) setPanel({ kind: 'detail', stop, stopNumber })
  }

  function selectMapMarker(stopId: string) {
    const stop = day.stops.find((item) => item.id === stopId)
    if (stop) selectStop(stop, day.stops.findIndex((item) => item.id === stopId) + 1)
  }

  function handleChooseCandidate(candidate: DemoTripStop, replacementId?: string) {
    if (replacementId) {
      applyEdit({ type: 'replace-stop', dayIndex, stopId: replacementId, replacement: candidate })
      setSelectedStopId(replacementId)
    } else {
      const id = `${candidate.id}-${Date.now()}`
      applyEdit({ type: 'add-stop', dayIndex, stop: { ...candidate, id } })
      setSelectedStopId(id)
    }
    setPanel(null)
  }

  function handleTransportChange(stopId: string, mode: DemoTransportMode) {
    applyEdit({ type: 'set-transport', dayIndex, stopId, transport: mode })
    setPanel(null)
  }

  function handleMoveDay(stopId: string, targetDayIndex: number) {
    applyEdit({ type: 'move-stop-to-day', fromDayIndex: dayIndex, toDayIndex: targetDayIndex, stopId })
    setDayIndex(targetDayIndex)
    setPanel(null)
  }

  function handleTimeChange(stopId: string, time: string, duration: number) {
    applyEdit({ type: 'set-time-and-duration', dayIndex, stopId, time, duration })
    setPanel(null)
  }

  function handleMenuAction(stopId: string, action: 'replace' | 'time' | 'move' | 'delete') {
    const stop = day.stops.find((item) => item.id === stopId)
    if (!stop) return
    if (action === 'replace') setPanel({ kind: 'add', replacementId: stop.id })
    else if (action === 'time') setPanel({ kind: 'time', stop })
    else if (action === 'move') setPanel({ kind: 'move', stopId })
    else {
      applyEdit({ type: 'delete-stop', dayIndex, stopId })
      setPanel(null)
      setNotice('地点已从本页演示行程移除，没有保存到账号。')
    }
  }

  function addSuggestion(candidateId: string) {
    const candidate = TRAVEL_CANDIDATES.find((item) => item.id === candidateId)
    if (candidate) handleChooseCandidate(candidate)
  }

  function addDay() {
    applyEdit({ type: 'add-day' }, '已添加演示日程，仅在当前页面展示。')
    setDayIndex(days.length)
    setSelectedStopId('')
  }

  function changeDay(index: number) {
    setDayIndex(index)
    setSelectedStopId(days[index]?.stops[0]?.id ?? '')
    setRevealedStopId(null)
  }

  function enterEditMode() {
    router.push(`/travel/itineraries/${encodeURIComponent(tripId)}/edit`)
  }

  function finishEditMode() {
    setSortMode(false)
    router.push(`/travel/itineraries/${encodeURIComponent(tripId)}`)
  }

  function submitChat(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!chatDraft.trim()) return
    setChatSent((current) => [...current, chatDraft.trim()])
    setChatDraft('')
  }

  const timeline = day ? <TripTimeline
    day={day}
    selectedStopId={selectedStopId}
    editing={editMode}
    sortMode={sortMode}
    revealedStopId={revealedStopId}
    onSelectStop={selectStop}
    onOpenPanel={setPanel}
    onMoveStop={(fromIndex, toIndex) => applyEdit({ type: 'move-stop', dayIndex, fromIndex, toIndex }, '行程顺序已在本页演示调整。')}
    onDeleteStop={(stop) => {
      applyEdit({ type: 'delete-stop', dayIndex, stopId: stop.id }, '地点已从本页演示行程移除，没有保存到账号。')
      setPanel(null)
    }}
    onSwipeReveal={(stop) => setRevealedStopId((current) => current === stop.id ? null : stop.id)}
    onLongPress={() => { setSortMode(true); setNotice('已进入排序模式，可用箭头调整顺序。演示修改不会保存到账号。') }}
    onAddPlace={() => setPanel({ kind: 'add' })}
  /> : null

  const desktopMap = day ? <TravelRouteMap stops={day.stops} selectedStopId={selectedStopId} onSelectStop={selectMapMarker} dayLabel={`第 ${day.day} 天`} /> : null
  const mobileMap = day ? <TravelRouteMap stops={day.stops} selectedStopId={selectedStopId} onSelectStop={selectMapMarker} dayLabel={`第 ${day.day} 天`} compact /> : null

  return (
    <main className="flex min-h-screen flex-col bg-[#f4f8fd] text-[#17283f] lg:h-screen lg:min-h-0">
      <header className="hidden h-[78px] shrink-0 items-center justify-between gap-4 border-b border-[#e6edf5] bg-white px-5 lg:flex xl:px-7">
        <div className="flex min-w-0 items-center gap-3">
          <a href="/travel" aria-label="返回旅游首页" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#6b7f98] hover:bg-[#f3f7fc]"><ArrowLeft size={17} aria-hidden /></a>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><MapPin size={17} aria-hidden /></span>
          <div className="min-w-0"><h1 className="truncate text-base font-bold text-[#233a56]">{state.itinerary.destination} · {days.length}天{Math.max(0, days.length - 1)}晚</h1><p className="mt-1 truncate text-[10px] text-[#8493a6]">{days[0]?.date}出发 <span className="mx-1 text-[#d1dae5]">·</span>{state.itinerary.people}人 <span className="mx-1 text-[#d1dae5]">·</span>预算约 ¥{state.itinerary.budget} <span className="mx-1 text-[#d1dae5]">·</span>{state.itinerary.style}</p></div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="rounded-full bg-[#f1f6fc] px-3 py-1.5 text-[9px] font-medium text-[#6380a0]">示例行程</span>
          {state.hasLocalEdits && <button type="button" onClick={() => { undo(); setNotice('已撤销上一步演示修改，当前内容仍未保存到账号。') }} className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-[#dce6f1] bg-white px-3 text-[10px] font-medium text-[#5b718d]"><RotateCcw size={13} aria-hidden />撤销</button>}
          {editMode ? <button type="button" onClick={finishEditMode} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3.5 text-[10px] font-semibold text-white"><Check size={14} aria-hidden />完成编辑</button> : <button type="button" onClick={enterEditMode} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3.5 text-[10px] font-semibold text-white"><SlidersHorizontal size={14} aria-hidden />编辑行程</button>}
        </div>
      </header>

      <header className="sticky top-0 z-20 flex h-[56px] shrink-0 items-center justify-between border-b border-[#e5ecf4] bg-white px-3 lg:hidden">
        <a href="/travel" aria-label="返回旅游首页" className="flex h-9 w-9 items-center justify-center rounded-full text-[#536984]"><ArrowLeft size={18} aria-hidden /></a>
        <div className="min-w-0 px-2 text-center"><h1 className="truncate text-sm font-semibold text-[#263b55]">{state.itinerary.destination} · {days.length}天行程</h1><p className="mt-0.5 text-[9px] text-[#92a0b1]">{editMode ? '演示编辑模式' : '行程详情'}</p></div>
        {editMode ? <button type="button" onClick={finishEditMode} className="rounded-full bg-[#2878f5] px-3 py-2 text-[10px] font-semibold text-white">完成</button> : <button type="button" onClick={enterEditMode} className="rounded-full bg-[#2878f5] px-3 py-2 text-[10px] font-semibold text-white">编辑</button>}
      </header>

      <div className="hidden min-h-0 flex-1 gap-3 p-3 lg:grid lg:grid-cols-[292px_minmax(425px,.92fr)_minmax(400px,1.08fr)] xl:grid-cols-[310px_minmax(440px,.92fr)_minmax(440px,1.08fr)]">
        <TripChatPanel onAddSuggestion={addSuggestion} />
        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]">
          <header className="shrink-0 border-b border-[#edf1f6] px-4 pb-3 pt-3.5">
            <div className="mb-3 flex items-center justify-between"><div><h2 className="text-sm font-bold text-[#263b55]">行程时间轴</h2><p className="mt-1 text-[9px] text-[#8a98aa]">按天安排 · 地点与地图编号联动</p></div><div className="flex items-center gap-1.5"><button type="button" onClick={() => setSortMode((value) => !value)} aria-pressed={sortMode} className={`rounded-lg px-2.5 py-2 text-[9px] font-medium ${sortMode ? 'bg-[#eaf2ff] text-[#2878f5]' : 'bg-[#f4f7fb] text-[#73849a]'}`}>{sortMode ? '退出排序' : '排序'}</button><button type="button" onClick={() => setEditMenuOpen((value) => !value)} aria-label="行程操作" className="flex h-8 w-8 items-center justify-center rounded-lg text-[#8292a6] hover:bg-[#f3f7fc]"><MoreHorizontal size={16} aria-hidden /></button></div></div>
            {editMenuOpen && <div className="mb-3 flex items-center justify-between rounded-xl border border-[#e5edf6] bg-[#f9fbfe] px-3 py-2"><span className="text-[9px] text-[#7789a0]">修改后支持撤销</span><button type="button" onClick={addDay} className="inline-flex items-center gap-1 text-[9px] font-medium text-[#2878f5]"><Plus size={12} aria-hidden />增加一天</button></div>}
            <div className="flex gap-2 overflow-x-auto pb-1">
              {days.map((item, index) => <button key={item.day} type="button" onClick={() => changeDay(index)} aria-pressed={dayIndex === index} className={`min-w-[88px] rounded-xl border px-3 py-2 text-left ${dayIndex === index ? 'border-[#76aaf4] bg-[#f2f7ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#6e8097]'}`}><span className="block text-[10px] font-semibold">第 {item.day} 天</span><span className="mt-1 block text-[8px]">{item.date}</span></button>)}
              <button type="button" onClick={addDay} aria-label="增加一天" className="flex h-[44px] min-w-[48px] items-center justify-center rounded-xl border border-dashed border-[#c9d9eb] text-[#2878f5]"><Plus size={16} aria-hidden /></button>
            </div>
          </header>
          <div className="flex min-h-0 flex-1 flex-col px-4 pb-3 pt-3">
            <div className="mb-3 flex shrink-0 items-center justify-between"><div><p className="text-[11px] font-semibold text-[#344b67]">第 {day?.day ?? 1} 天 · {day?.date}</p><p className="mt-1 text-[9px] text-[#93a0b1]">{day?.stops.length ?? 0} 个地点 · 留出休息时间</p></div>{sortMode && <span className="rounded-full bg-[#edf5ff] px-2.5 py-1 text-[8px] font-medium text-[#4d82bf]">排序模式</span>}</div>
            {notice && <p role="status" className="mb-2 shrink-0 rounded-lg bg-[#f2f7fd] px-2.5 py-2 text-[9px] leading-4 text-[#587494]">{notice}</p>}
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">{timeline}</div>
          </div>
        </section>
        <aside className="flex min-h-0 min-w-0 flex-col gap-3 rounded-[22px] border border-[#e4ebf4] bg-white p-3 shadow-[0_6px_24px_rgba(31,74,130,.05)]">
          <div className="flex items-center justify-between px-1"><div><h2 className="text-sm font-bold text-[#263b55]">路线地图</h2><p className="mt-1 text-[9px] text-[#8a98aa]">点击编号定位行程地点</p></div><span className="rounded-full bg-[#f1f6fc] px-2.5 py-1 text-[9px] text-[#7589a1]">第 {day?.day ?? 1} 天</span></div>
          <div className="shrink-0">{desktopMap}</div>
          {activeStop ? <div className="flex min-h-0 flex-1 gap-3 overflow-hidden rounded-[18px] border border-[#e8edf4] bg-white p-2.5">
            <img src={activeStop.image} alt="" className="h-[112px] w-[130px] shrink-0 rounded-xl object-cover" />
            <div className="flex min-w-0 flex-1 flex-col py-0.5"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-[8px] font-medium text-[#5b89c0]">行程地点详情 · 示例内容</p><h3 className="mt-1 truncate text-sm font-semibold text-[#2b405b]">{activeStop.title}</h3></div><span className="rounded-md bg-[#edf5ff] px-2 py-1 text-[8px] text-[#5683b8]">{activeStop.category}</span></div><p className="mt-2 line-clamp-3 text-[10px] leading-5 text-[#75869a]">{activeStop.description}</p><div className="mt-auto flex items-center gap-3 text-[9px] text-[#8292a6]"><span className="inline-flex items-center gap-1"><Clock3 size={11} aria-hidden />{activeStop.time}</span><span>停留 {activeStop.duration} 分钟</span></div></div>
          </div> : <div className="flex flex-1 items-center justify-center text-xs text-[#91a0b2]">选择地点查看详情</div>}
          <div className="flex items-center justify-between border-t border-[#edf1f6] px-1 pt-2 text-[8px] text-[#96a3b2]"><span>示意路线 · 非实际道路导航</span><span>地图双向联动</span></div>
        </aside>
      </div>

      <div className="flex-1 px-3 pb-24 pt-3 lg:hidden">
        <div className="mb-3"><DemoNotice compact /></div>
        <section className="mb-3 rounded-[20px] border border-[#e4ebf4] bg-white p-3.5 shadow-[0_4px_16px_rgba(31,74,130,.04)]">
          <div className="flex items-start justify-between gap-3"><div><p className="text-[9px] font-medium text-[#5a87bd]">示例行程 · 仅供界面体验</p><h2 className="mt-1 text-base font-bold text-[#263b55]">杭州湖山慢游</h2><p className="mt-1 text-[10px] text-[#8796a8]">10月18日 · {days.length}天{Math.max(0, days.length - 1)}晚 · {state.itinerary.people}人</p></div><button type="button" onClick={() => setEditMenuOpen((value) => !value)} aria-label="行程详情操作" className="flex h-8 w-8 items-center justify-center rounded-full bg-[#f3f7fc] text-[#74869d]"><MoreHorizontal size={16} aria-hidden /></button></div>
          <div className="mt-3 grid grid-cols-3 divide-x divide-[#e8edf4] rounded-xl bg-[#f7faff] py-2.5"><div className="text-center"><p className="text-[8px] text-[#91a0b1]">人数</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">{state.itinerary.people} 位</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">预算参考</p><p className="mt-1 text-[10px] font-semibold text-[#4b6380]">¥{state.itinerary.budget}</p></div><div className="text-center"><p className="text-[8px] text-[#91a0b1]">旅行节奏</p><p className="mt-1 truncate px-1 text-[10px] font-semibold text-[#4b6380]">轻松慢游</p></div></div>
        </section>

        <div className="mb-3 flex items-center gap-2 overflow-x-auto pb-1">
          {days.map((item, index) => <button key={item.day} type="button" onClick={() => changeDay(index)} aria-pressed={dayIndex === index} className={`min-w-[92px] rounded-xl border px-3 py-2 text-center ${dayIndex === index ? 'border-[#79aaf2] bg-[#edf5ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#788ba3]'}`}><span className="block text-[10px] font-semibold">第 {item.day} 天</span><span className="mt-1 block text-[8px]">{item.date}</span></button>)}
          <button type="button" aria-label="增加一天" onClick={addDay} className="flex h-[44px] min-w-[42px] items-center justify-center rounded-xl border border-dashed border-[#cbd9e8] bg-white text-[#2878f5]"><Plus size={16} aria-hidden /></button>
        </div>

        <div className="mb-3 flex items-center justify-between rounded-xl border border-[#e5ecf5] bg-white p-1">
          {[['all', '地图 + 行程'], ['map', '地图'], ['timeline', '行程']].map(([value, label]) => <button key={value} type="button" aria-pressed={mobileFocus === value} onClick={() => setMobileFocus(value as 'all' | 'map' | 'timeline')} className={`h-8 flex-1 rounded-lg text-[10px] ${mobileFocus === value ? 'bg-[#edf5ff] font-semibold text-[#2878f5]' : 'text-[#7e8da1]'}`}>{label}</button>)}
          <button type="button" aria-pressed={sortMode} onClick={() => setSortMode((value) => !value)} className={`ml-1 flex h-8 items-center gap-1 rounded-lg px-2 text-[9px] ${sortMode ? 'bg-[#eaf2ff] font-semibold text-[#2878f5]' : 'text-[#75859a]'}`}><SlidersHorizontal size={12} aria-hidden />排序</button>
        </div>

        {notice && <p role="status" className="mb-3 rounded-lg bg-[#f2f7fd] px-3 py-2 text-[9px] leading-4 text-[#587494]">{notice}</p>}
        {(mobileFocus === 'all' || mobileFocus === 'map') && <section aria-label="当天行程地图" className="mb-3">{mobileMap}</section>}
        {(mobileFocus === 'all' || mobileFocus === 'timeline') && <section className="rounded-[20px] border border-[#e4ebf4] bg-white p-3 shadow-[0_4px_16px_rgba(31,74,130,.04)]">
          <header className="mb-3 flex items-center justify-between"><div><h2 className="text-xs font-bold text-[#2d435e]">第 {day?.day ?? 1} 天安排</h2><p className="mt-1 text-[9px] text-[#92a0b1]">{day?.stops.length ?? 0} 个地点 · 点按查看详情</p></div>{state.hasLocalEdits && <button type="button" onClick={() => { undo(); setNotice('已撤销最近一次演示修改，当前内容仍未保存到账号。') }} className="inline-flex items-center gap-1 rounded-lg bg-[#f3f7fc] px-2.5 py-2 text-[9px] font-medium text-[#637a96]"><RotateCcw size={11} aria-hidden />撤销</button>}</header>
          {timeline}
        </section>}
        <div className="mt-3 flex items-center justify-between px-1 text-[9px] text-[#94a2b2]"><span className="inline-flex items-center gap-1"><Wallet size={11} aria-hidden />预算仅供参考，演示内容未核实</span><button type="button" onClick={() => setEditMenuOpen((value) => !value)} className="inline-flex items-center gap-1 text-[#6486ac]">更多操作 <ChevronDown size={11} aria-hidden /></button></div>
        {editMenuOpen && <div className="mt-2 grid grid-cols-2 gap-2 rounded-xl border border-[#e5edf6] bg-white p-3"><button type="button" onClick={addDay} className="rounded-lg bg-[#f3f7fc] px-3 py-2.5 text-[10px] text-[#58718f]">增加一天</button><button type="button" onClick={enterEditMode} className="rounded-lg bg-[#edf5ff] px-3 py-2.5 text-[10px] font-medium text-[#2878f5]">打开编辑模式</button></div>}
      </div>

      <div className="pointer-events-none fixed left-0 right-0 top-[58px] z-0 hidden justify-center lg:flex"><span className="rounded-full border border-[#e1ebf7] bg-white/90 px-2.5 py-1 text-[8px] text-[#7c8fa7]">{editMode ? '编辑模式 · 演示修改未同步' : '地图路线为示意连线'}</span></div>
      <div className="fixed bottom-[72px] left-3 right-3 z-20 lg:hidden">{editMode && state.hasLocalEdits && <button type="button" onClick={() => { undo(); setNotice('已撤销最近一次演示修改，当前内容仍未保存到账号。') }} className="flex h-9 w-full items-center justify-center gap-1.5 rounded-full border border-[#dbe7f4] bg-white/95 text-[10px] font-medium text-[#607791] shadow-sm"><RotateCcw size={12} aria-hidden />撤销上一步演示修改</button>}</div>
      <TravelBottomNav active="trips" />
      <TripEditPanels
        key={panelKey}
        panel={panel}
        days={days}
        activeDayIndex={dayIndex}
        onClose={() => setPanel(null)}
        onChooseCandidate={handleChooseCandidate}
        onTransportChange={handleTransportChange}
        onMoveDay={handleMoveDay}
        onTimeChange={handleTimeChange}
        onMenuAction={handleMenuAction}
      />
    </main>
  )
}
