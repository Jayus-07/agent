'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, ArrowRight, CalendarDays, Check, Clock3, LoaderCircle, MapPin, Users } from 'lucide-react'
import { copyTravelTemplateV2, fetchTravelTemplateV2, type TravelTemplateV2 } from '@/api/travelV2'
import { TRAVEL_PHOTOS } from './travelV2Data'
import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'

export default function TravelTemplatePreview({ slug }: { slug: string }) {
  const router = useRouter()
  const [template, setTemplate] = useState<TravelTemplateV2 | null>(null)
  const [loading, setLoading] = useState(true)
  const [copying, setCopying] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    void fetchTravelTemplateV2(slug).then((data) => {
      if (!cancelled) setTemplate(data)
    }).catch(() => {
      if (!cancelled) setError('模板详情暂时无法加载，请返回首页重试。')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [slug])

  async function createCopy() {
    if (!template || copying) return
    setCopying(true)
    setError('')
    try {
      const result = await copyTravelTemplateV2(template.slug)
      router.push(`/travel/itineraries/${encodeURIComponent(result.trip_id)}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '行程副本创建失败，请稍后重试。')
    } finally {
      setCopying(false)
    }
  }

  const destination = template?.destination || template?.content.brief.destination || ''
  const image = template?.cover_image_url || destinationImage(destination)
  const brief = template?.content.brief
  const itemsCount = template?.content.days.reduce((total, day) => total + day.items.length, 0) ?? 0
  const budget = brief?.budget.amount

  return <main className="min-h-screen bg-[#f4f8fd] pb-24 text-[#17283f] md:pb-10">
    <TravelTopBar />
    <div className="mx-auto max-w-[1120px] px-4 pb-8 pt-1 md:px-8 md:pt-3">
      <a href="/travel" className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-[#6c7d93] hover:text-[#2878f5]"><ArrowLeft size={14} aria-hidden />返回旅行首页</a>
      {loading ? <div role="status" className="rounded-[26px] border border-[#e4ebf4] bg-white p-12 text-center text-sm text-[#8393a8]">正在读取平台模板…</div> : error && !template ? <div role="alert" className="rounded-[26px] border border-amber-200 bg-amber-50 p-8 text-center text-sm text-amber-900">{error}</div> : template && <section className="overflow-hidden rounded-[26px] border border-[#e4ebf4] bg-white shadow-[0_12px_36px_rgba(31,74,130,.06)] md:rounded-[32px]">
        <div className="relative h-[230px] md:h-[330px]"><img src={image} alt={`${destination}风景`} className="absolute inset-0 h-full w-full object-cover" /><div className="absolute inset-0 bg-gradient-to-t from-[#102843]/75 via-[#173451]/10 to-transparent" /><span className="absolute left-4 top-4 inline-flex items-center gap-1.5 rounded-full border border-white/50 bg-white/90 px-3 py-1.5 text-[10px] font-semibold text-[#35699f] shadow-sm"><Check size={12} aria-hidden />平台精选模板 · 只读预览</span><div className="absolute bottom-5 left-5 right-5 text-white md:bottom-7 md:left-8"><p className="text-xs font-medium text-white/80">{brief?.day_count} 天 · {template.tags.join(' · ') || '精选路线'}</p><h1 className="mt-1 text-[26px] font-bold tracking-[-.035em] md:text-[38px]">{template.title}</h1><p className="mt-2 max-w-xl text-xs leading-5 text-white/85 md:text-sm">{template.summary}</p></div></div>
        <div className="grid gap-6 p-4 md:grid-cols-[1fr_300px] md:p-7">
          <div><div className="mb-4 flex items-center justify-between gap-3"><div><h2 className="text-base font-bold text-[#20344e]">路线预览</h2><p className="mt-1 text-[10px] text-[#8392a6]">来自平台公开模板 · 复制后生成个人 V2 行程</p></div><span className="rounded-full bg-[#f0f6ff] px-3 py-1.5 text-[10px] font-medium text-[#4a7eb8]">{itemsCount} 个安排</span></div>
            <div className="space-y-5">{template.content.days.map((day, dayIndex) => <section key={day.day_id}><h3 className="mb-2 text-xs font-bold text-[#3d5877]">第 {dayIndex + 1} 天{day.date ? ` · ${day.date}` : ''}</h3><div className="space-y-3">{day.items.map((item, index) => <article key={item.item_id} className="relative flex gap-3 rounded-2xl border border-[#e8eef5] bg-[#fbfdff] p-3.5 md:p-4"><div className="flex w-10 shrink-0 flex-col items-center"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#2878f5] text-xs font-semibold text-white">{index + 1}</span>{index < day.items.length - 1 && <span className="mt-2 h-8 border-l border-dashed border-[#9fc2f2]" />}</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold text-[#243953]">{item.title}</h4><span className="text-[10px] font-medium text-[#7690ad]">{item.start_time || '时间待定'}</span></div><p className="mt-1.5 text-xs leading-5 text-[#7a8ba0]">{item.note || item.place?.address || item.activity_type || '行程安排'}</p><div className="mt-2 flex items-center gap-2 text-[10px] text-[#94a2b3]"><Clock3 size={12} aria-hidden />建议停留约 {item.duration_min} 分钟 <span className="text-[#d0d8e2]">·</span><MapPin size={12} aria-hidden />{item.place?.address || '地点信息待核实'}</div></div></article>)}</div>{day.legs.length > 0 && <p className="mt-2 text-[9px] text-[#8596aa]">路段交通：{day.legs.map((leg) => leg.selected_mode).join(' · ')}</p>}</section>)}</div>
          </div>
          <aside className="flex flex-col gap-3 rounded-[20px] bg-[#f6f9fd] p-4 md:p-5"><h2 className="text-sm font-bold text-[#263b56]">行程概览</h2><dl className="space-y-3 text-xs"><div className="flex justify-between"><dt className="flex items-center gap-2 text-[#8492a4]"><CalendarDays size={14} aria-hidden />行程天数</dt><dd className="font-medium text-[#354b66]">{brief?.day_count} 天</dd></div><div className="flex justify-between"><dt className="flex items-center gap-2 text-[#8492a4]"><Users size={14} aria-hidden />参考人数</dt><dd className="font-medium text-[#354b66]">{(brief?.travelers.adults ?? 0) + (brief?.travelers.children ?? 0)} 人</dd></div><div className="flex justify-between"><dt className="text-[#8492a4]">预算参考</dt><dd className="font-medium text-[#354b66]">{budget === null || budget === undefined ? '待设置' : `¥${budget.toLocaleString()}`}</dd></div><div className="border-t border-[#e4ebf3] pt-3"><dt className="mb-2 text-[#8492a4]">旅行风格</dt><dd className="flex flex-wrap gap-1.5">{template.tags.map((tag) => <span key={tag} className="rounded-full bg-white px-2.5 py-1 text-[10px] text-[#6884a4]">{tag}</span>)}</dd></div></dl>
            <div className="mt-auto rounded-xl border border-[#dce9fb] bg-white p-3 text-[10px] leading-5 text-[#71839a]">模板副本会保存到你的 V2 行程。酒店、车次与景点没有真实核实的数据会明确标记为待核实。</div>
            {error && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-[10px] leading-4 text-rose-700">{error}</p>}
            <button type="button" onClick={() => void createCopy()} disabled={copying} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#2878f5] text-xs font-semibold text-white shadow-[0_6px_16px_rgba(40,120,245,.2)] transition hover:bg-[#1769e9] disabled:cursor-wait disabled:opacity-70">{copying ? <><LoaderCircle size={15} className="animate-spin" aria-hidden />正在创建副本…</> : <>复制为我的行程 <ArrowRight size={15} aria-hidden /></>}</button>
          </aside>
        </div>
      </section>}
    </div>
    <TravelBottomNav active="home" />
  </main>
}

function destinationImage(destination: string): string {
  return /厦门|海|青岛|三亚/.test(destination) ? TRAVEL_PHOTOS.coast : /苏州|园林/.test(destination) ? TRAVEL_PHOTOS.garden : /杭州|西湖/.test(destination) ? TRAVEL_PHOTOS.westLake : TRAVEL_PHOTOS.city
}
