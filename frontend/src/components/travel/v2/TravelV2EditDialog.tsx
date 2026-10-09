'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRightLeft, Bus, CarFront, Clock3, Footprints, MapPin, Plus, Search, Trash2, X } from 'lucide-react'
import {
  searchTravelPlacesV2,
  type PlaceSearchResultV2,
  type TravelSearchStatusV2,
  type TravelTripV2,
  type TripEditOperationV2,
  type TripItemV2,
  type TripLegModeV2,
} from '@/api/travelV2'

export type TravelV2EditPanel =
  | { kind: 'item'; itemId: string }
  | { kind: 'add' }
  | { kind: 'transport'; fromItemId: string; toItemId: string }
  | null

const placeStatus: Record<TravelSearchStatusV2, string> = {
  success: '检索成功',
  no_results: '查询成功，但没有匹配地点',
  business_failure: '地点查询失败',
  network_timeout: '查询超时',
  provider_unavailable: '地点数据源不可用',
  disabled: '地点检索未启用',
}

const transportChoices: Array<{ mode: TripLegModeV2; label: string; icon: typeof Bus }> = [
  { mode: 'walk', label: '步行', icon: Footprints },
  { mode: 'transit', label: '公共交通', icon: Bus },
  { mode: 'drive', label: '驾车', icon: CarFront },
  { mode: 'taxi', label: '出租车', icon: CarFront },
]

export default function TravelV2EditDialog({
  panel,
  trip,
  dayId,
  item,
  route,
  saving,
  error,
  onClose,
  onEdit,
}: {
  panel: TravelV2EditPanel
  trip: TravelTripV2
  dayId: string
  item: TripItemV2 | null
  route?: { fromItemId: string; toItemId: string }
  saving: boolean
  error: string
  onClose: () => void
  onEdit: (operation: TripEditOperationV2, summary: string) => Promise<boolean>
}) {
  const [view, setView] = useState<'menu' | 'add' | 'search' | 'activity' | 'time' | 'move' | 'transport'>('add')
  const [keyword, setKeyword] = useState('')
  const [querying, setQuerying] = useState(false)
  const [searchResponse, setSearchResponse] = useState<Awaited<ReturnType<typeof searchTravelPlacesV2>> | null>(null)
  const [searchError, setSearchError] = useState('')
  const [activityType, setActivityType] = useState<NonNullable<TripItemV2['activity_type']>>('custom')
  const [activityTitle, setActivityTitle] = useState('')
  const [startTime, setStartTime] = useState('')
  const [duration, setDuration] = useState(90)
  const [note, setNote] = useState('')

  useEffect(() => {
    setView(panel?.kind === 'item' ? 'menu' : panel?.kind === 'transport' ? 'transport' : 'add')
    setKeyword('')
    setSearchResponse(null)
    setSearchError('')
    setNote(item?.note ?? '')
    setStartTime(item?.start_time ?? '')
    setDuration(item?.duration_min ?? 90)
    setActivityTitle('')
  }, [panel, item?.item_id])

  if (!panel) return null

  const selectedItem = panel.kind === 'item' ? item : null
  const locked = Boolean(selectedItem?.locked)
  const fixed = Boolean(selectedItem?.fixed_start)
  const itemDay = selectedItem ? trip.document.days.find((day) => day.items.some((entry) => entry.item_id === selectedItem.item_id)) : null
  const itemIndex = itemDay?.items.findIndex((entry) => entry.item_id === selectedItem?.item_id) ?? -1
  const title = view === 'menu' ? selectedItem?.title ?? '编辑行程项'
    : view === 'activity' ? '添加活动'
      : view === 'time' ? '调整时间与停留'
        : view === 'move' ? '移动到其他日期'
          : view === 'transport' ? '选择路段交通'
            : view === 'search' ? (selectedItem ? '替换地点' : '搜索地点')
              : '添加地点或活动'

  async function save(operation: TripEditOperationV2, summary: string) {
    if (await onEdit(operation, summary)) onClose()
  }

  async function searchPlaces(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!keyword.trim() || querying) return
    setQuerying(true)
    setSearchError('')
    setSearchResponse(null)
    try {
      const response = await searchTravelPlacesV2(trip.document.brief.destination, keyword.trim())
      setSearchResponse(response)
    } catch (cause) {
      setSearchError(cause instanceof Error ? cause.message : '查询未完成，行程没有修改。')
    } finally {
      setQuerying(false)
    }
  }

  async function choosePlace(candidate: PlaceSearchResultV2) {
    if (selectedItem) {
      await save({
        op: 'replace_place', item_id: selectedItem.item_id,
        selection_id: candidate.selection_id, place: candidate.place,
      }, `已将「${selectedItem.title}」替换为「${candidate.place.name}」`)
      return
    }
    await save({
      op: 'add_place', day_id: dayId, selection_id: candidate.selection_id,
      place: candidate.place, start_time: startTime || null,
      duration_min: duration,
    }, `已添加「${candidate.place.name}」`)
  }

  async function submitActivity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cleanTitle = activityTitle.trim()
    if (!cleanTitle) return
    await save({
      op: 'add_activity', day_id: dayId, activity_type: activityType,
      title: cleanTitle, start_time: startTime || null,
      duration_min: duration, note,
    }, `已添加「${cleanTitle}」`)
  }

  async function submitTime(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedItem) return
    await save({
      op: 'update_item', item_id: selectedItem.item_id,
      start_time: startTime || null, duration_min: duration, note,
    }, `已更新「${selectedItem.title}」时间`)
  }

  async function reorderItem(delta: -1 | 1) {
    if (!itemDay || itemIndex < 0 || itemIndex + delta < 0 || itemIndex + delta >= itemDay.items.length || !selectedItem) return
    const itemIds = itemDay.items.map((entry) => entry.item_id)
    ;[itemIds[itemIndex], itemIds[itemIndex + delta]] = [itemIds[itemIndex + delta], itemIds[itemIndex]]
    await save({ op: 'reorder_day', day_id: itemDay.day_id, item_ids: itemIds }, `已调整「${selectedItem.title}」顺序`)
  }

  const placeResults = searchResponse?.results ?? []

  return <div className="fixed inset-0 z-[60] flex items-end justify-center bg-[#17283f]/35 p-2 backdrop-blur-[2px] sm:items-center sm:p-5" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose() }}>
    <section role="dialog" aria-modal="true" aria-label={title} className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-t-[24px] border border-[#e4ebf4] bg-white p-4 shadow-[0_24px_80px_rgba(15,36,62,.22)] sm:rounded-[24px] sm:p-5">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          {view !== 'menu' && panel.kind !== 'transport' && <button type="button" aria-label="返回" onClick={() => setView(selectedItem ? 'menu' : 'add')} className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#f2f6fb] text-[#6f839c]"><ArrowLeft size={14} aria-hidden /></button>}
          <div><h2 className="text-sm font-bold text-[#263b55]">{title}</h2><p className="mt-1 text-[10px] leading-5 text-[#8191a5]">{view === 'search' ? '地点名称与位置来自实时检索；营业时间和价格待核实。' : '每次修改会提交到正式行程；失败时保留当前版本。'}</p></div>
        </div>
        <button type="button" aria-label="关闭编辑面板" disabled={saving} onClick={onClose} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#f3f6fa] text-[#74859b] disabled:opacity-50"><X size={15} aria-hidden /></button>
      </header>

      {error && <p role="alert" className="mb-3 rounded-xl bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700">{error}</p>}
      {saving && <p role="status" className="mb-3 rounded-xl bg-[#eff6ff] px-3 py-2 text-[11px] text-[#356fae]">正在保存正式行程…</p>}

      {view === 'menu' && selectedItem && <div className="grid grid-cols-2 gap-2">
        <button type="button" disabled={saving || locked} onClick={() => setView('search')} className="flex h-11 items-center gap-2 rounded-xl bg-[#f2f7fd] px-3 text-left text-[11px] font-medium text-[#49627f] disabled:opacity-50"><ArrowRightLeft size={14} aria-hidden />替换地点</button>
        <button type="button" disabled={saving || locked} onClick={() => setView('time')} className="flex h-11 items-center gap-2 rounded-xl bg-[#f2f7fd] px-3 text-left text-[11px] font-medium text-[#49627f] disabled:opacity-50"><Clock3 size={14} aria-hidden />时间与停留</button>
        <button type="button" disabled={saving || locked || fixed || itemIndex <= 0} onClick={() => void reorderItem(-1)} className="h-11 rounded-xl bg-[#f2f7fd] px-3 text-left text-[11px] font-medium text-[#49627f] disabled:opacity-50">同日上移</button>
        <button type="button" disabled={saving || locked || fixed || !itemDay || itemIndex >= itemDay.items.length - 1} onClick={() => void reorderItem(1)} className="h-11 rounded-xl bg-[#f2f7fd] px-3 text-left text-[11px] font-medium text-[#49627f] disabled:opacity-50">同日下移</button>
        <button type="button" disabled={saving || locked || fixed || trip.document.days.length < 2} onClick={() => setView('move')} className="h-11 rounded-xl bg-[#f2f7fd] px-3 text-left text-[11px] font-medium text-[#49627f] disabled:opacity-50">移动到其他天</button>
        <button type="button" disabled={saving || locked || fixed} onClick={() => void save({ op: 'remove_item', item_id: selectedItem.item_id }, `已删除「${selectedItem.title}」`)} className="flex h-11 items-center gap-2 rounded-xl bg-[#fff2f1] px-3 text-left text-[11px] font-medium text-[#b95750] disabled:opacity-50"><Trash2 size={14} aria-hidden />删除行程项</button>
        {(locked || fixed) && <p className="col-span-2 rounded-lg bg-[#f8f9fb] px-3 py-2 text-[10px] leading-4 text-[#78899e]">{locked ? '此项已锁定，不能替换、移动或删除。' : '此项固定了开始时间，不能移动或删除。'}</p>}
      </div>}

      {view === 'add' && <div className="grid gap-2 sm:grid-cols-2">
        <button type="button" onClick={() => setView('search')} className="flex min-h-16 items-center gap-3 rounded-xl border border-[#e5edf6] bg-[#fbfdff] p-3 text-left hover:border-[#a9c9f2]"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#edf5ff] text-[#2878f5]"><Search size={16} aria-hidden /></span><span><span className="block text-xs font-semibold text-[#344b66]">搜索真实地点</span><span className="mt-1 block text-[9px] text-[#8695a7]">接入现有腾讯 POI Provider</span></span></button>
        <button type="button" onClick={() => setView('activity')} className="flex min-h-16 items-center gap-3 rounded-xl border border-[#e5edf6] bg-[#fbfdff] p-3 text-left hover:border-[#a9c9f2]"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#edf5ff] text-[#2878f5]"><Plus size={16} aria-hidden /></span><span><span className="block text-xs font-semibold text-[#344b66]">添加自定义活动</span><span className="mt-1 block text-[9px] text-[#8695a7]">餐饮、休息、购物或自由活动</span></span></button>
        <button type="button" disabled={saving || trip.document.days.length >= 60} onClick={() => void save({ op: 'add_day' }, '已添加一天')} className="min-h-12 rounded-xl border border-dashed border-[#a9c7ed] bg-[#f6faff] text-xs font-semibold text-[#2878f5] disabled:opacity-50">＋ 添加一天</button>
        <button type="button" disabled={saving || trip.document.days.length <= 1 || Boolean(trip.document.days.find((day) => day.day_id === dayId)?.items.some((entry) => entry.locked || entry.fixed_start))} onClick={() => void save({ op: 'remove_day', day_id: dayId }, '已删除当天安排')} className="min-h-12 rounded-xl border border-[#f0d8d6] bg-[#fffafa] text-xs font-medium text-[#ae625d] disabled:opacity-50">删除当前天</button>
      </div>}

      {view === 'search' && <div>
        <form onSubmit={(event) => void searchPlaces(event)} className="flex gap-2">
          <label htmlFor="travel-v2-place-keyword" className="sr-only">地点关键词</label>
          <input id="travel-v2-place-keyword" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder={`在${trip.document.brief.destination}搜索地点`} className="h-11 min-w-0 flex-1 rounded-xl border border-[#e1e9f3] bg-[#fbfdff] px-3 text-xs text-[#344b66] outline-none focus:border-[#8db6f8]" />
          <button type="submit" disabled={querying || !keyword.trim()} className="flex h-11 shrink-0 items-center gap-1.5 rounded-xl bg-[#2878f5] px-3 text-xs font-semibold text-white disabled:opacity-50"><Search size={14} aria-hidden />{querying ? '查询中' : '查询'}</button>
        </form>
        {!selectedItem && <div className="mt-3 rounded-xl bg-[#fbfdff] p-3"><p className="mb-2 text-[10px] font-semibold text-[#6d8098]">加入第 {trip.document.days.findIndex((day) => day.day_id === dayId) + 1} 天</p><TimeFields startTime={startTime} duration={duration} note={note} onStartTime={setStartTime} onDuration={setDuration} onNote={setNote} /></div>}
        {searchError && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[10px] text-rose-700">{searchError}</p>}
        {searchResponse && <div className="mt-3 rounded-lg bg-[#f3f7fc] px-3 py-2 text-[10px] leading-4 text-[#657b95]"><span className="font-semibold">{placeStatus[searchResponse.status]}</span> · {searchResponse.message}<p className="mt-1 text-[9px] text-[#8796a8]">{searchResponse.disclosure}</p></div>}
        {placeResults.length > 0 && <div className="mt-3 max-h-[45vh] space-y-2 overflow-y-auto">
          {placeResults.map((candidate) => <button key={candidate.selection_id} type="button" disabled={saving} onClick={() => void choosePlace(candidate)} className="flex w-full items-center gap-3 rounded-xl border border-[#e7edf5] p-3 text-left transition hover:border-[#a9c9f2] hover:bg-[#f8fbff] disabled:opacity-50"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#edf5ff] text-[#2878f5]"><MapPin size={16} aria-hidden /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-[#344b66]">{candidate.place.name}</span><span className="mt-1 block truncate text-[10px] text-[#8292a6]">{candidate.place.address || candidate.category || '地点地址待核实'}</span><span className="mt-1 block text-[9px] text-[#a0aaba]">坐标来源：{candidate.source} · 营业时间和价格未知</span></span><Plus size={15} className="shrink-0 text-[#2878f5]" aria-hidden /></button>)}
        </div>}
      </div>}

      {view === 'activity' && <form onSubmit={(event) => void submitActivity(event)} className="space-y-3">
        <label className="block text-[10px] font-medium text-[#70829a]">活动类型<select value={activityType} onChange={(event) => setActivityType(event.target.value as NonNullable<TripItemV2['activity_type']>)} className="mt-1.5 h-10 w-full rounded-xl border border-[#e1e9f3] bg-white px-3 text-xs text-[#344b66]"><option value="meal">餐饮</option><option value="rest">休息</option><option value="shopping">购物</option><option value="free_time">自由活动</option><option value="custom">自定义活动</option></select></label>
        <label className="block text-[10px] font-medium text-[#70829a]">名称<input required maxLength={200} value={activityTitle} onChange={(event) => setActivityTitle(event.target.value)} placeholder="例如：午餐或咖啡休息" className="mt-1.5 h-10 w-full rounded-xl border border-[#e1e9f3] bg-white px-3 text-xs text-[#344b66]" /></label>
        <TimeFields startTime={startTime} duration={duration} note={note} onStartTime={setStartTime} onDuration={setDuration} onNote={setNote} />
        <button type="submit" disabled={saving || !activityTitle.trim()} className="h-11 w-full rounded-xl bg-[#2878f5] text-xs font-semibold text-white disabled:opacity-50">{saving ? '保存中…' : '添加并保存'}</button>
      </form>}

      {view === 'time' && selectedItem && <form onSubmit={(event) => void submitTime(event)} className="space-y-3">
        {fixed && <p className="rounded-lg bg-amber-50 px-3 py-2 text-[10px] text-amber-800">该行程项固定了开始时间，只能修改停留时长或说明。</p>}
        <TimeFields startTime={startTime} duration={duration} note={note} onStartTime={setStartTime} onDuration={setDuration} onNote={setNote} disableTime={fixed} />
        <button type="submit" disabled={saving} className="h-11 w-full rounded-xl bg-[#2878f5] text-xs font-semibold text-white disabled:opacity-50">{saving ? '保存中…' : '应用并保存'}</button>
      </form>}

      {view === 'move' && selectedItem && <div className="space-y-2">{trip.document.days.map((day, index) => <button key={day.day_id} type="button" disabled={saving || day.day_id === dayId} onClick={() => void save({ op: 'move_item', item_id: selectedItem.item_id, target_day_id: day.day_id }, `已将「${selectedItem.title}」移动到第 ${index + 1} 天`)} className="flex w-full items-center justify-between rounded-xl border border-[#e7edf5] px-4 py-3 text-left text-xs text-[#465b75] enabled:hover:border-[#a9c9f2] enabled:hover:bg-[#f8fbff] disabled:bg-[#f3f7fc] disabled:text-[#94a3b5]">第 {index + 1} 天 <span>{day.date || '日期待定'}{day.day_id === dayId ? ' · 当前' : ''}</span></button>)}</div>}

      {view === 'transport' && route && <div className="space-y-2">{transportChoices.map(({ mode, label, icon: Icon }) => <button key={mode} type="button" disabled={saving} onClick={() => void save({ op: 'set_leg_mode', from_item_id: route.fromItemId, to_item_id: route.toItemId, selected_mode: mode }, `已将路段交通设为${label}`)} className="flex w-full items-center gap-3 rounded-xl border border-[#e7edf5] p-3 text-left hover:border-[#a9c9f2] hover:bg-[#f8fbff] disabled:opacity-50"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#edf5ff] text-[#2878f5]"><Icon size={17} aria-hidden /></span><span><span className="block text-xs font-semibold text-[#344b66]">{label}</span><span className="mt-1 block text-[9px] text-[#8493a5]">路线时长、距离和费用清除为未知，待真实路线查询</span></span></button>)}</div>}
    </section>
  </div>
}

function TimeFields({
  startTime, duration, note, disableTime = false,
  onStartTime, onDuration, onNote,
}: {
  startTime: string
  duration: number
  note: string
  disableTime?: boolean
  onStartTime: (value: string) => void
  onDuration: (value: number) => void
  onNote: (value: string) => void
}) {
  return <div className="grid gap-3 sm:grid-cols-2">
    <label className="block text-[10px] font-medium text-[#70829a]">开始时间<input type="time" disabled={disableTime} value={startTime} onChange={(event) => onStartTime(event.target.value)} className="mt-1.5 h-10 w-full rounded-xl border border-[#e1e9f3] bg-white px-3 text-xs text-[#344b66] disabled:bg-[#f3f6fa]" /></label>
    <label className="block text-[10px] font-medium text-[#70829a]">停留分钟<input type="number" min={0} max={1440} step={15} value={duration} onChange={(event) => onDuration(Number(event.target.value))} className="mt-1.5 h-10 w-full rounded-xl border border-[#e1e9f3] bg-white px-3 text-xs text-[#344b66]" /></label>
    <label className="block text-[10px] font-medium text-[#70829a] sm:col-span-2">说明<input maxLength={2000} value={note} onChange={(event) => onNote(event.target.value)} placeholder="可选" className="mt-1.5 h-10 w-full rounded-xl border border-[#e1e9f3] bg-white px-3 text-xs text-[#344b66]" /></label>
  </div>
}
