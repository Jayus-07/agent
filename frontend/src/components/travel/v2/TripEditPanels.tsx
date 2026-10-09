'use client'

import { useState } from 'react'
import { ArrowRightLeft, Bus, CarFront, Clock3, Footprints, MapPin, Plus, Search, X } from 'lucide-react'
import { TRAVEL_CANDIDATES } from './travelV2Data'
import type { DemoTripDay, DemoTripStop, DemoTransportMode } from './travelV2State'

export type TripEditPanel =
  | { kind: 'add'; replacementId?: string }
  | { kind: 'menu'; stop: DemoTripStop }
  | { kind: 'transport'; stopId: string }
  | { kind: 'move'; stopId: string }
  | { kind: 'time'; stop: DemoTripStop }
  | { kind: 'detail'; stop: DemoTripStop; stopNumber: number }
  | null

export default function TripEditPanels({
  panel, days, activeDayIndex, onClose, onChooseCandidate, onTransportChange, onMoveDay, onTimeChange, onMenuAction,
}: {
  panel: TripEditPanel
  days: DemoTripDay[]
  activeDayIndex: number
  onClose: () => void
  onChooseCandidate: (candidate: DemoTripStop, replacementId?: string) => void
  onTransportChange: (stopId: string, mode: DemoTransportMode) => void
  onMoveDay: (stopId: string, targetDayIndex: number) => void
  onTimeChange: (stopId: string, time: string, duration: number) => void
  onMenuAction: (stopId: string, action: 'replace' | 'time' | 'move' | 'delete') => void
}) {
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('全部')
  const [time, setTime] = useState(panel?.kind === 'time' ? panel.stop.time : '09:00')
  const [duration, setDuration] = useState(panel?.kind === 'time' ? panel.stop.duration : 90)
  if (!panel) return null

  const title = panel.kind === 'add' ? (panel.replacementId ? '替换地点' : '添加地点或活动')
    : panel.kind === 'transport' ? '选择路段交通'
      : panel.kind === 'move' ? '移动到哪一天'
        : panel.kind === 'time' ? '调整时间与停留时长'
          : panel.kind === 'menu' ? panel.stop.title : panel.stop.title

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-[#152841]/35 p-0 backdrop-blur-[2px] md:items-center md:p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section role="dialog" aria-modal="true" aria-label={title} className="max-h-[86vh] w-full overflow-y-auto rounded-t-[26px] border border-[#e4ebf4] bg-white p-4 shadow-[0_24px_80px_rgba(15,36,62,.22)] md:max-w-[480px] md:rounded-[24px] md:p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div><h2 className="text-base font-bold text-[#203650]">{title}</h2><p className="mt-1 text-[10px] text-[#8a98aa]">演示操作只修改当前页面内容</p></div>
          <button type="button" onClick={onClose} aria-label="关闭" className="flex h-8 w-8 items-center justify-center rounded-full bg-[#f4f7fb] text-[#74859b]"><X size={16} aria-hidden /></button>
        </div>

        {panel.kind === 'add' && (
          <div>
            <div className="flex h-10 items-center gap-2 rounded-xl border border-[#e1e9f3] bg-[#fbfdff] px-3"><Search size={15} className="text-[#91a1b5]" aria-hidden /><label className="sr-only" htmlFor="candidate-search">搜索景点、餐饮或活动</label><input id="candidate-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索景点、餐饮或活动" className="h-full min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-[#a5b1c0]" /></div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">{['全部', '景点', '餐饮', '休息', '活动'].map((item) => <button key={item} type="button" aria-pressed={category === item} onClick={() => setCategory(item)} className={`shrink-0 rounded-full px-3 py-1.5 text-[10px] ${category === item ? 'bg-[#2878f5] font-semibold text-white' : 'bg-[#f3f6fa] text-[#708199]'}`}>{item}</button>)}</div>
            <div className="mt-3 space-y-2">
              {TRAVEL_CANDIDATES.filter((candidate) => (category === '全部' || candidate.category === category) && candidate.title.includes(search.trim())).map((candidate) => (
                <button key={candidate.id} type="button" onClick={() => onChooseCandidate(candidate, panel.replacementId)} className="flex w-full items-center gap-3 rounded-xl border border-[#e9eef5] p-2 text-left transition hover:border-[#a9c9f2] hover:bg-[#f8fbff]">
                  <img src={candidate.image} alt="" className="h-12 w-14 shrink-0 rounded-lg object-cover" />
                  <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-[#344b66]">{candidate.title}</span><span className="mt-1 block truncate text-[10px] text-[#8493a5]">{candidate.description}</span></span>
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#edf5ff] text-[#2878f5]">{panel.replacementId ? <ArrowRightLeft size={13} aria-hidden /> : <Plus size={14} aria-hidden />}</span>
                </button>
              ))}
              {TRAVEL_CANDIDATES.filter((candidate) => (category === '全部' || candidate.category === category) && candidate.title.includes(search.trim())).length === 0 && <p className="py-8 text-center text-xs text-[#8291a4]">没有匹配的演示地点</p>}
            </div>
            <p className="mt-3 text-[9px] text-[#9ba8b8]">景点候选为本地示例，尚未连接真实检索。</p>
          </div>
        )}

        {panel.kind === 'transport' && (
          <div className="space-y-2">
            {[
              { mode: 'walk' as const, label: '步行', note: '适合相邻街区与园区内短距离', icon: Footprints },
              { mode: 'transit' as const, label: '公交', note: '演示选择，不展示未核实的时长与费用', icon: Bus },
              { mode: 'drive' as const, label: '驾车', note: '演示选择，不展示未核实的时长与费用', icon: CarFront },
            ].map(({ mode, label, note, icon: Icon }) => <button key={mode} type="button" onClick={() => onTransportChange(panel.stopId, mode)} className="flex w-full items-center gap-3 rounded-xl border border-[#e7edf5] p-3 text-left hover:border-[#a9c9f2] hover:bg-[#f8fbff]"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#edf5ff] text-[#2878f5]"><Icon size={17} aria-hidden /></span><span className="flex-1"><span className="block text-xs font-semibold text-[#344b66]">{label}</span><span className="mt-1 block text-[9px] text-[#8493a5]">{note}</span></span><span className="h-4 w-4 rounded-full border border-[#c8d5e4]" /></button>)}
          </div>
        )}

        {panel.kind === 'menu' && (
          <div className="grid grid-cols-2 gap-2">
            {[
              { action: 'replace' as const, label: '替换地点' },
              { action: 'time' as const, label: '修改时间' },
              { action: 'move' as const, label: '移动到其他天' },
              { action: 'delete' as const, label: '删除地点' },
            ].map(({ action, label }) => <button key={action} type="button" onClick={() => onMenuAction(panel.stop.id, action)} className={`h-11 rounded-xl text-xs font-medium ${action === 'delete' ? 'bg-[#fff3f2] text-[#cc4c4c]' : 'bg-[#f3f7fc] text-[#47627f]'}`}>{label}</button>)}
          </div>
        )}

        {panel.kind === 'move' && (
          <div className="space-y-2">
            {days.map((day, index) => <button key={day.day} type="button" disabled={index === activeDayIndex} onClick={() => onMoveDay(panel.stopId, index)} className="flex w-full items-center justify-between rounded-xl border border-[#e7edf5] px-4 py-3 text-left text-xs text-[#465b75] enabled:hover:border-[#a9c9f2] enabled:hover:bg-[#f8fbff] disabled:bg-[#f3f7fc] disabled:text-[#94a3b5]">第 {day.day} 天 <span>{day.date}{index === activeDayIndex ? ' · 当前' : ''}</span></button>)}
          </div>
        )}

        {panel.kind === 'time' && (
          <form onSubmit={(event) => { event.preventDefault(); onTimeChange(panel.stop.id, time, duration) }} className="space-y-4">
            <label className="block text-xs text-[#71839a]">开始时间<input type="time" value={time} onChange={(event) => setTime(event.target.value)} className="mt-1.5 h-11 w-full rounded-xl border border-[#e1e9f3] bg-[#fbfdff] px-3 text-sm text-[#344b66] outline-none focus:border-[#8db6f8]" /></label>
            <label className="block text-xs text-[#71839a]">停留时长<input type="number" min={15} step={15} value={duration} onChange={(event) => setDuration(Number(event.target.value))} className="mt-1.5 h-11 w-full rounded-xl border border-[#e1e9f3] bg-[#fbfdff] px-3 text-sm text-[#344b66] outline-none focus:border-[#8db6f8]" /></label>
            <button type="submit" className="h-11 w-full rounded-xl bg-[#2878f5] text-xs font-semibold text-white">应用演示调整</button>
          </form>
        )}

        {panel.kind === 'detail' && (
          <div>
            <div className="relative h-[180px] overflow-hidden rounded-2xl bg-[#edf3f8]"><img src={panel.stop.image} alt={panel.stop.title} className="h-full w-full object-cover" /><span className="absolute left-3 top-3 rounded-full bg-white/95 px-2.5 py-1 text-[10px] font-semibold text-[#2878f5]">第 {panel.stopNumber} 站</span></div>
            <div className="mt-4 flex items-start justify-between gap-3"><div><h3 className="text-base font-bold text-[#253a54]">{panel.stop.title}</h3><p className="mt-1 text-[10px] text-[#8392a6]">{panel.stop.category ?? '行程地点'} · 建议停留 {panel.stop.duration} 分钟</p></div><span className="rounded-lg bg-[#f1f6fc] px-2 py-1 text-[10px] text-[#6884a4]">{panel.stop.time}</span></div>
            <p className="mt-3 text-xs leading-6 text-[#6f8197]">{panel.stop.description ?? '地点详情会在接入真实景点数据后展示。'}</p>
            <div className="mt-4 flex items-center gap-2 rounded-xl bg-[#f4f8fd] p-3 text-[10px] text-[#8292a6]"><MapPin size={14} className="text-[#2878f5]" aria-hidden />位置、营业时间与路线信息暂为演示内容</div>
          </div>
        )}

        {panel.kind === 'time' && <p className="mt-3 flex items-center gap-1.5 text-[9px] text-[#94a1b2]"><Clock3 size={12} aria-hidden />这里不会检查营业时间冲突，也不会向服务器提交。</p>}
      </section>
    </div>
  )
}
