'use client'

import { useRef } from 'react'
import { ArrowDown, ArrowUp, Bus, CarFront, Clock3, Footprints, GripVertical, MoreHorizontal, Plus, Trash2 } from 'lucide-react'
import type { DemoTripDay, DemoTripStop, DemoTransportMode } from './travelV2State'

const TRANSPORT_LABEL: Record<DemoTransportMode, string> = { walk: '步行', transit: '公交', drive: '驾车' }
const TRANSPORT_ICON = { walk: Footprints, transit: Bus, drive: CarFront }

export function TransportLabel({ mode, compact = false }: { mode: DemoTransportMode; compact?: boolean }) {
  const Icon = TRANSPORT_ICON[mode]
  return <span className={`inline-flex items-center gap-1 text-[#72849b] ${compact ? 'text-[9px]' : 'text-[10px]'}`}><Icon size={compact ? 11 : 13} aria-hidden />{TRANSPORT_LABEL[mode]}</span>
}

function StopRow({ stop, index, total, selected, editing, sortMode, revealed, transportAfter, onSelect, onMenu, onLongPress, onSwipeReveal, onTransportClick, onMove, onDelete }: {
  stop: DemoTripStop
  index: number
  total: number
  selected: boolean
  editing: boolean
  sortMode: boolean
  revealed: boolean
  transportAfter: DemoTransportMode
  onSelect: () => void
  onMenu: () => void
  onLongPress: () => void
  onSwipeReveal: () => void
  onTransportClick: () => void
  onMove: (fromIndex: number, toIndex: number) => void
  onDelete: (stop: DemoTripStop) => void
}) {
  const touchStart = useRef<number | null>(null)
  const touchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function clearLongPress() {
    if (touchTimer.current) clearTimeout(touchTimer.current)
    touchTimer.current = null
  }

  return (
    <div className="relative overflow-hidden rounded-[17px]">
      {revealed && <button type="button" aria-label={`删除${stop.title}`} onClick={() => onDelete(stop)} className="absolute inset-y-0 right-0 z-0 flex w-[72px] items-center justify-center gap-1 bg-[#e94f55] text-[10px] font-semibold text-white"><Trash2 size={14} aria-hidden />删除</button>}
      <article
        onTouchStart={(event) => {
          touchStart.current = event.touches[0]?.clientX ?? null
          clearLongPress()
          touchTimer.current = setTimeout(onLongPress, 520)
        }}
        onTouchMove={clearLongPress}
        onTouchEnd={(event) => {
          clearLongPress()
          const end = event.changedTouches[0]?.clientX
          if (touchStart.current !== null && end !== undefined && touchStart.current - end > 52) onSwipeReveal()
          touchStart.current = null
        }}
        onTouchCancel={clearLongPress}
        className={`relative z-10 flex items-stretch gap-2.5 rounded-[17px] border bg-white p-2.5 transition-transform duration-200 ${selected ? 'border-[#78aaf4] shadow-[0_4px_14px_rgba(40,120,245,.11)]' : 'border-[#e8edf4]'} ${revealed ? '-translate-x-[72px]' : ''}`}
      >
        <div className="flex w-7 shrink-0 flex-col items-center pt-1">
          <button type="button" onClick={onSelect} aria-label={`地图定位到第 ${index + 1} 站 ${stop.title}`} className={`flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold text-white ${selected ? 'bg-[#175fc5] ring-4 ring-[#dceaff]' : 'bg-[#2878f5]'}`}>{index + 1}</button>
          {index < total - 1 && <span className="mt-1 min-h-[58px] flex-1 border-l border-dashed border-[#bed3ee]" />}
        </div>
        <button type="button" onClick={onSelect} className="flex min-w-0 flex-1 items-center gap-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5]">
          <img src={stop.image} alt="" loading="lazy" className="h-[58px] w-[66px] shrink-0 rounded-xl bg-[#edf3f8] object-cover sm:h-[62px] sm:w-[76px]" />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-1.5"><span className="truncate text-xs font-semibold text-[#2b405b]">{stop.title}</span>{stop.category && <span className="rounded-md bg-[#edf6f2] px-1.5 py-0.5 text-[8px] font-medium text-[#39806a]">{stop.category}</span>}</span>
            <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[9px] text-[#8190a3]"><span>{stop.time}</span><span className="text-[#d0d8e1]">·</span><span className="inline-flex items-center gap-1"><Clock3 size={10} aria-hidden />{stop.duration}分钟</span></span>
            <span className="mt-1 block truncate text-[9px] text-[#98a5b4]">{stop.description ?? '行程地点详情'}</span>
          </span>
        </button>
        {sortMode && <div className="flex shrink-0 flex-col items-center justify-center gap-1"><GripVertical size={15} className="text-[#92a2b6]" aria-hidden /><button type="button" aria-label={`上移${stop.title}`} disabled={index === 0} onClick={() => onMove(index, index - 1)} className="rounded-md p-1 text-[#6681a2] hover:bg-[#f1f6fc] disabled:text-[#d5dde6]"><ArrowUp size={13} aria-hidden /></button><button type="button" aria-label={`下移${stop.title}`} disabled={index === total - 1} onClick={() => onMove(index, index + 1)} className="rounded-md p-1 text-[#6681a2] hover:bg-[#f1f6fc] disabled:text-[#d5dde6]"><ArrowDown size={13} aria-hidden /></button></div>}
        {!sortMode && <button type="button" aria-label={`编辑${stop.title}`} onClick={onMenu} className="flex h-8 w-7 shrink-0 items-center justify-center rounded-lg text-[#8798ad] hover:bg-[#f3f7fc]"><MoreHorizontal size={17} aria-hidden /></button>}
      </article>
      {index < total - 1 && (
        <button type="button" onClick={onTransportClick} aria-label={`切换${stop.title}之后的交通方式`} className="relative z-10 ml-[47px] flex h-8 items-center gap-2 px-1 text-left">
          <span className="h-px w-4 border-t border-dashed border-[#bdd2ec]" />
          <TransportLabel mode={transportAfter} compact />
          <span className="text-[9px] text-[#9aa8b8]">点击选择路段交通</span>
        </button>
      )}
    </div>
  )
}

export default function TripTimeline({ day, selectedStopId, editing, sortMode, revealedStopId, onSelectStop, onOpenPanel, onMoveStop, onDeleteStop, onSwipeReveal, onLongPress, onAddPlace }: {
  day: DemoTripDay
  selectedStopId: string
  editing: boolean
  sortMode: boolean
  revealedStopId: string | null
  onSelectStop: (stop: DemoTripStop, stopNumber: number) => void
  onOpenPanel: (panel: { kind: 'add'; replacementId?: string } | { kind: 'menu'; stop: DemoTripStop } | { kind: 'time'; stop: DemoTripStop } | { kind: 'move'; stopId: string } | { kind: 'transport'; stopId: string }) => void
  onMoveStop: (fromIndex: number, toIndex: number) => void
  onDeleteStop: (stop: DemoTripStop) => void
  onSwipeReveal: (stop: DemoTripStop) => void
  onLongPress: () => void
  onAddPlace: () => void
}) {
  return (
    <div className="space-y-1.5">
      {day.stops.map((stop, index) => (
        <div key={stop.id}>
          <StopRow
            stop={stop} index={index} total={day.stops.length} selected={selectedStopId === stop.id}
            editing={editing} sortMode={sortMode} revealed={revealedStopId === stop.id}
            transportAfter={day.stops[index + 1]?.transport ?? 'walk'}
            onSelect={() => onSelectStop(stop, index + 1)} onMove={onMoveStop} onDelete={onDeleteStop}
            onSwipeReveal={() => onSwipeReveal(stop)} onLongPress={onLongPress}
            onTransportClick={() => onOpenPanel({ kind: 'transport', stopId: day.stops[index + 1]?.id ?? stop.id })}
            onMenu={() => onOpenPanel({ kind: 'menu', stop })}
          />
          {editing && !sortMode && <div className="-mt-0.5 mb-1 ml-[47px] flex gap-2">
            <button type="button" onClick={() => onOpenPanel({ kind: 'time', stop })} className="rounded-lg border border-[#e4ebf4] px-2 py-1 text-[9px] text-[#6e829b] hover:border-[#b5cdee]">改时间</button>
            <button type="button" onClick={() => onOpenPanel({ kind: 'add', replacementId: stop.id })} className="rounded-lg border border-[#e4ebf4] px-2 py-1 text-[9px] text-[#6e829b] hover:border-[#b5cdee]">替换</button>
            <button type="button" onClick={() => onOpenPanel({ kind: 'move', stopId: stop.id })} className="rounded-lg border border-[#e4ebf4] px-2 py-1 text-[9px] text-[#6e829b] hover:border-[#b5cdee]">移到其他天</button>
          </div>}
        </div>
      ))}
      <button type="button" onClick={onAddPlace} className="mt-2 flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[#a9c7ed] bg-[#f6faff] text-xs font-semibold text-[#2878f5] transition hover:bg-[#edf5ff]"><Plus size={15} aria-hidden />添加地点或活动</button>
      {day.stops.length === 0 && <div className="rounded-xl border border-dashed border-[#dce6f2] bg-[#fbfdff] p-6 text-center text-xs text-[#8797aa]">这一天还没有安排。添加地点、餐饮或活动。</div>}
    </div>
  )
}
