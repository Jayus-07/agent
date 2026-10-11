'use client'

import { useState } from 'react'
import { Hotel, TrainFront, Utensils } from 'lucide-react'
import TravelSearchPanel, { type TravelSearchKind } from './TravelSearchPanel'
import { PageHeading, TravelBottomNav, TravelTopBar } from './TravelV2Frame'

type DiscoverKind = Exclude<TravelSearchKind, 'trip'>

const categories: Array<{ kind: DiscoverKind; label: string; description: string; icon: typeof Hotel }> = [
  { kind: 'food', label: '美食', description: '查找目的地餐厅与用餐地点', icon: Utensils },
  { kind: 'hotel', label: '酒店', description: '查看住宿地点参考，不代表房态可订', icon: Hotel },
  { kind: 'train', label: '动车', description: '按出发站、到达站和日期查询车次', icon: TrainFront },
]

export default function TravelDiscover() {
  const [kind, setKind] = useState<DiscoverKind>('food')
  const activeCategory = categories.find((category) => category.kind === kind) ?? categories[0]

  return <main className="min-h-screen bg-[#f4f8fd] pb-24 text-[#17283f] md:pb-10">
    <TravelTopBar active="discover" />
    <div className="mx-auto max-w-[1000px] px-4 pb-8 pt-2 md:px-8 md:pt-4">
      <PageHeading eyebrow="发现旅途" title="先查一查，再决定" description="酒店、美食和动车查询是只读操作，不会修改或创建正式行程。" />
      <nav aria-label="发现类别" className="mt-5 grid grid-cols-3 gap-2 sm:gap-3">
        {categories.map(({ kind: categoryKind, label, description, icon: Icon }) => <button key={categoryKind} type="button" aria-pressed={kind === categoryKind} onClick={() => setKind(categoryKind)} className={`flex min-h-[76px] min-w-0 items-center gap-2 rounded-2xl border p-3 text-left transition motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5] sm:min-h-[84px] sm:gap-3 sm:p-4 ${kind === categoryKind ? 'border-[#9bbef1] bg-white shadow-[0_6px_20px_rgba(31,74,130,.07)]' : 'border-[#e4ebf3] bg-white/70 hover:border-[#bfd6f5]'}`}>
          <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl sm:h-10 sm:w-10 ${kind === categoryKind ? 'bg-[#eaf2ff] text-[#2878f5]' : 'bg-[#f2f6fb] text-[#6e8199]'}`}><Icon size={18} aria-hidden /></span>
          <span className="min-w-0"><span className="block text-xs font-semibold text-[#304761] sm:text-sm">{label}</span><span className="mt-1 hidden text-[10px] leading-4 text-[#8493a6] sm:block">{description}</span></span>
        </button>)}
      </nav>
      <p className="mt-3 rounded-xl border border-[#e4ebf3] bg-white/70 px-3 py-2 text-[10px] leading-4 text-[#75879d]">{activeCategory.description}。实际来源、查询时间和可用状态以服务端响应为准。</p>
      <div className="mt-3"><TravelSearchPanel key={kind} initialKind={kind} showTabs={false} /></div>
    </div>
    <TravelBottomNav active="discover" />
  </main>
}
