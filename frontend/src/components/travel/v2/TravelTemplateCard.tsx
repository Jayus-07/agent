import Link from 'next/link'
import { ArrowRight, CalendarDays, Users } from 'lucide-react'
import type { TravelTemplateV2 } from '@/api/travelV2'
import { TRAVEL_PHOTOS } from './travelV2Data'

export function TravelTemplateCard({ template, layout = 'grid' }: {
  template: TravelTemplateV2
  layout?: 'carousel' | 'grid'
}) {
  const days = template.content.brief.day_count
  const destination = template.destination || template.content.brief.destination
  const image = template.cover_image_url || travelDestinationImage(destination)
  const budget = template.content.brief.budget.amount
  const style = template.tags.slice(0, 2).join(' · ') || '精选路线'
  const layoutClass = layout === 'carousel'
    ? 'w-[80vw] max-w-[320px] shrink-0 snap-start md:w-auto md:max-w-none'
    : 'w-full'

  return <Link href={`/travel/templates/${encodeURIComponent(template.slug)}`} className={`group block min-w-0 overflow-hidden rounded-[22px] border border-[#e6edf5] bg-white transition hover:-translate-y-0.5 hover:border-[#c7dcf8] hover:shadow-[0_12px_30px_rgba(30,72,125,.1)] motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5] ${layoutClass}`}>
    <div className="relative h-[150px] overflow-hidden md:h-[178px]">
      <img src={image} alt={`${destination}旅行风景`} loading="lazy" className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.03] motion-reduce:transition-none motion-reduce:group-hover:scale-100" />
      <div className="absolute inset-0 bg-gradient-to-t from-[#122944]/55 to-transparent" />
      <span className="absolute left-3 top-3 rounded-full bg-white/90 px-2.5 py-1 text-[10px] font-semibold text-[#376aab]">{days}天{Math.max(0, days - 1)}晚</span>
      <span className="absolute bottom-3 left-3 text-lg font-semibold text-white">{destination}</span>
      <span className="absolute bottom-3 right-3 max-w-[55%] truncate text-[10px] text-white/90">{style}</span>
    </div>
    <div className="p-3.5">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold text-[#243953]">{template.title}</span>
          <span className="mt-1 line-clamp-2 block min-h-8 text-[10px] leading-4 text-[#7a8ba0]">{template.summary || '打开查看完整路线预览。'}</span>
        </span>
        <ArrowRight size={15} className="mt-0.5 shrink-0 text-[#91a4bb] transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" aria-hidden />
      </div>
      <span className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#edf1f6] pt-2.5 text-[9px] text-[#8797aa]">
        <span className="inline-flex items-center gap-1"><CalendarDays size={11} aria-hidden />{days} 天</span>
        <span className="inline-flex items-center gap-1"><Users size={11} aria-hidden />{(template.content.brief.travelers.adults ?? 0) + (template.content.brief.travelers.children ?? 0)} 人</span>
        <span>{budget === null || budget === undefined ? '预算待设置' : `预算约 ¥${budget.toLocaleString()}`}</span>
      </span>
    </div>
  </Link>
}

export function travelDestinationImage(destination: string): string {
  return /厦门|海|青岛|三亚/.test(destination) ? TRAVEL_PHOTOS.coast
    : /苏州|园林/.test(destination) ? TRAVEL_PHOTOS.garden
      : /杭州|西湖/.test(destination) ? TRAVEL_PHOTOS.westLake
        : TRAVEL_PHOTOS.city
}
