'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, ArrowUpRight, CalendarDays, Hotel, MapPin, MessageCircle, Plane, Sparkles, TrainFront, Utensils } from 'lucide-react'
import { archiveTravelTripV2, fetchTravelTripsV2, fetchTravelTemplatesV2, type TravelTemplateV2, type TravelTripV2 } from '@/api/travelV2'
import { TRAVEL_PHOTOS } from './travelV2Data'
import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import TravelSearchPanel, { type TravelSearchKind } from './TravelSearchPanel'

export default function TravelHome() {
  const [templates, setTemplates] = useState<TravelTemplateV2[]>([])
  const [trips, setTrips] = useState<TravelTripV2[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(true)
  const [tripsLoading, setTripsLoading] = useState(true)
  const [templateError, setTemplateError] = useState('')
  const [tripError, setTripError] = useState('')
  const [searchKind, setSearchKind] = useState<Exclude<TravelSearchKind, 'trip'> | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchTravelTemplatesV2().then((data) => {
      if (!cancelled) {
        setTemplates(data.templates ?? [])
        setTemplatesLoading(false)
      }
    }).catch(() => {
      if (!cancelled) {
        setTemplateError('模板目录暂时无法加载，请稍后重试。')
        setTemplatesLoading(false)
      }
    })
    void fetchTravelTripsV2().then((data) => {
      if (!cancelled) {
        setTrips(data.trips ?? [])
        setTripsLoading(false)
      }
    }).catch(() => {
      if (!cancelled) {
        setTripError('最近行程暂时无法加载。')
        setTripsLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [])

  return (
    <main className="min-h-screen bg-[#f4f8fd] pb-24 text-[#17283f] md:pb-10">
      <TravelTopBar />
      <div className="mx-auto max-w-[1280px] px-4 pb-8 pt-1 md:px-8 md:pt-3">
        <section className="relative overflow-hidden rounded-[28px] border border-[#e1ebf8] bg-white shadow-[0_14px_44px_rgba(28,76,140,.07)] md:rounded-[34px]">
          <div className="absolute -right-12 -top-28 h-72 w-72 rounded-full bg-[#e4f0ff] blur-2xl md:h-[430px] md:w-[430px]" />
          <div className="absolute -right-24 bottom-[-190px] h-80 w-[620px] rotate-[-12deg] rounded-[50%] bg-[#eff6ff]" />
          <div className="relative grid gap-5 px-5 py-7 sm:px-8 md:min-h-[290px] md:grid-cols-[1.15fr_.85fr] md:items-center md:px-12 md:py-10">
            <div>
              <div className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-[#eff6ff] px-3 py-1.5 text-[11px] font-semibold text-[#2878f5]"><Sparkles size={13} aria-hidden />把想去的地方，慢慢变成行程</div>
              <h1 className="max-w-xl text-[29px] font-bold leading-[1.2] tracking-[-.045em] text-[#162a45] sm:text-[34px] md:text-[42px]">下一段旅程，<br className="hidden sm:block" />从一个想法开始</h1>
              <p className="mt-3 max-w-lg text-sm leading-6 text-[#70819a] md:text-[15px]">说说你想去哪里、喜欢什么节奏。先看看灵感，再一起把每天安排得刚刚好。</p>
              <form action="/travel/chat" method="get" className="mt-5 rounded-2xl border border-[#dfebfa] bg-white p-2 shadow-[0_8px_26px_rgba(40,120,245,.1)] focus-within:border-[#8db6f8] focus-within:ring-4 focus-within:ring-[#2878f5]/[.08]">
                <label className="sr-only" htmlFor="travel-idea">描述你的旅行</label>
                <textarea id="travel-idea" name="prompt" aria-label="描述你的旅行" rows={2} placeholder="例如：周末去杭州两天，想看湖景、吃本地菜，行程轻松一点…" className="block w-full resize-none bg-transparent px-3 py-2 text-sm leading-6 text-[#253851] outline-none placeholder:text-[#a0aec0]" />
                <div className="flex items-center justify-between gap-3 px-1 pb-1"><span className="hidden items-center gap-2 text-[11px] text-[#8a9bb0] sm:flex"><MapPin size={13} aria-hidden />目的地不限，先从灵感聊起</span><span className="text-[10px] text-[#9aa8b9] sm:hidden">自然语言开始规划</span><button type="submit" className="inline-flex h-10 shrink-0 items-center gap-2 rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white shadow-[0_6px_16px_rgba(40,120,245,.24)] transition hover:bg-[#1769e9]"><MessageCircle size={15} aria-hidden />和 AI 聊聊<ArrowRight size={14} aria-hidden /></button></div>
              </form>
              <div className="mt-3 grid grid-cols-3 gap-2" aria-label="快捷查询">
                {[
                  { kind: 'hotel' as const, label: '查酒店', icon: Hotel },
                  { kind: 'food' as const, label: '查美食', icon: Utensils },
                  { kind: 'train' as const, label: '查动车', icon: TrainFront },
                ].map(({ kind, label, icon: Icon }) => <button key={kind} type="button" onClick={() => setSearchKind((current) => current === kind ? null : kind)} aria-expanded={searchKind === kind} className={`flex h-12 items-center justify-center gap-2 rounded-xl border bg-white text-xs font-semibold transition ${searchKind === kind ? 'border-[#8db6f8] bg-[#f4f8ff] text-[#2878f5]' : 'border-[#e4ebf3] text-[#536b86] hover:border-[#bfd6f5]'}`}><Icon size={16} aria-hidden />{label}</button>)}
              </div>
              {searchKind && <div className="mt-3"><TravelSearchPanel key={searchKind} initialKind={searchKind} showTabs={false} /></div>}
              <div className="mt-3 flex flex-wrap gap-2">{['杭州慢游', '亲子自然路线', '周末美食'].map((prompt) => <a key={prompt} href={`/travel/chat?prompt=${encodeURIComponent(prompt)}`} className="rounded-full border border-[#e4ebf3] bg-white/80 px-3 py-1.5 text-[10px] text-[#6f8097] transition hover:border-[#b9d3fa] hover:text-[#2878f5]">{prompt}</a>)}</div>
            </div>
            <div className="relative hidden h-[245px] overflow-hidden rounded-[25px] md:block"><img src={TRAVEL_PHOTOS.westLake} alt="杭州湖山风景" className="absolute inset-0 h-full w-full object-cover" /><div className="absolute inset-0 bg-gradient-to-t from-[#102a48]/60 via-transparent to-white/10" /><div className="absolute bottom-4 left-4 right-4 flex items-end justify-between text-white"><div><p className="text-xs text-white/75">此刻的灵感</p><p className="mt-1 text-lg font-semibold">杭州 · 湖山之间</p></div><span className="rounded-full border border-white/30 bg-white/20 px-3 py-1.5 text-[10px] backdrop-blur">慢慢走，刚刚好</span></div><span className="absolute right-4 top-4 rounded-full bg-white/90 px-3 py-1.5 text-[10px] font-semibold text-[#356aa9] shadow-sm">自然 · 人文 · 美食</span></div>
          </div>
        </section>

        <section className="mt-8 md:mt-10">
          <div className="mb-4 flex items-end justify-between gap-4"><div><p className="text-[10px] font-semibold text-[#4a82c5]">从一份好路线开始</p><h2 className="mt-1 text-lg font-bold tracking-[-.025em] text-[#17283f] md:text-[21px]">精选旅行模板</h2></div>{templates.length > 3 && <a href={`/travel/templates/${templates[0].slug}`} className="inline-flex items-center gap-1 text-xs font-medium text-[#2878f5]">浏览全部 <ArrowUpRight size={14} aria-hidden /></a>}</div>
          {templateError ? <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">{templateError}</p> : templatesLoading ? <p role="status" className="rounded-xl border border-[#e6edf5] bg-white px-4 py-6 text-center text-xs text-[#8292a6]">正在加载平台精选模板…</p> : templates.length === 0 ? <p role="status" className="rounded-xl border border-[#e6edf5] bg-white px-4 py-6 text-center text-xs text-[#8292a6]">暂时没有精选模板</p> : <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-2 md:mx-0 md:grid md:grid-cols-3 md:gap-4 md:overflow-visible md:px-0">{templates.slice(0, 6).map((template) => <TemplateCard key={template.slug} template={template} />)}</div>}
        </section>

        <section className="mt-8 md:mt-10">
          <div className="mb-4 flex items-end justify-between gap-4"><div><p className="text-[10px] font-semibold text-[#4a82c5]">接着上次的计划</p><h2 className="mt-1 text-lg font-bold tracking-[-.025em] text-[#17283f] md:text-[21px]">我的最近行程</h2></div><a href="/travel/itineraries" className="text-xs font-medium text-[#2878f5]">全部行程</a></div>
          {tripError ? <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">{tripError}</p> : tripsLoading ? <p role="status" className="rounded-xl border border-[#e6edf5] bg-white px-4 py-6 text-center text-xs text-[#8292a6]">正在加载最近行程…</p> : trips.length === 0 ? <div className="rounded-[20px] border border-dashed border-[#cfdeef] bg-white/70 p-6 text-center"><Plane size={22} className="mx-auto text-[#83a7d5]" aria-hidden /><p className="mt-2 text-sm font-semibold text-[#465e7a]">还没有已保存的行程</p><p className="mt-1 text-xs text-[#8a9bb0]">从上方描述旅行需求，或复制一份精选模板。</p></div> : <div className="space-y-3">{trips.slice(0, 3).map((trip) => <PlanCard key={trip.trip_id} trip={trip} templates={templates} onArchived={() => setTrips((current) => current.filter((item) => item.trip_id !== trip.trip_id))} />)}</div>}
        </section>
      </div>
      <TravelBottomNav active="home" />
    </main>
  )
}

function TemplateCard({ template }: { template: TravelTemplateV2 }) {
  const days = template.content.brief.day_count
  const destination = template.destination || template.content.brief.destination
  const image = template.cover_image_url || destinationImage(destination)
  const budget = template.content.brief.budget.amount
  const style = template.tags.slice(0, 2).join(' · ') || '精选路线'
  return <a href={`/travel/templates/${encodeURIComponent(template.slug)}`} className="group w-[80vw] max-w-[320px] shrink-0 snap-start overflow-hidden rounded-[22px] border border-[#e6edf5] bg-white transition hover:-translate-y-0.5 hover:border-[#c7dcf8] hover:shadow-[0_12px_30px_rgba(30,72,125,.1)] md:w-auto md:max-w-none"><div className="relative h-[150px] overflow-hidden md:h-[178px]"><img src={image} alt={`${destination}旅行风景`} loading="lazy" className="h-full w-full object-cover transition duration-500 group-hover:scale-[1.03]" /><div className="absolute inset-0 bg-gradient-to-t from-[#122944]/55 to-transparent" /><span className="absolute left-3 top-3 rounded-full bg-white/90 px-2.5 py-1 text-[10px] font-semibold text-[#376aab]">{days}天{Math.max(0, days - 1)}晚</span><span className="absolute bottom-3 left-3 text-lg font-semibold text-white">{destination}</span><span className="absolute bottom-3 right-3 text-[10px] text-white/85">{style}</span></div><div className="p-3.5 md:p-4"><div className="flex items-start justify-between gap-2"><div><h3 className="text-sm font-semibold text-[#20344e]">{template.title}</h3><p className="mt-1 text-[10px] text-[#8898ac]">{template.content.brief.travelers.adults + template.content.brief.travelers.children} 位出行 · {budget === null ? '预算待设置' : `预算 ¥${budget}`}</p></div><ArrowRight size={16} className="mt-1 shrink-0 text-[#98abc3] transition group-hover:translate-x-0.5 group-hover:text-[#2878f5]" aria-hidden /></div><div className="mt-3 flex gap-1.5">{template.tags.slice(0, 3).map((tag) => <span key={tag} className="rounded-md bg-[#f2f6fb] px-2 py-1 text-[9px] text-[#71839b]">{tag}</span>)}</div></div></a>
}

function PlanCard({ trip, templates, onArchived }: { trip: TravelTripV2; templates: TravelTemplateV2[]; onArchived: () => void }) {
  const destination = trip.document.brief.destination
  const template = templates.find((item) => item.destination === destination)
  const image = template?.cover_image_url || destinationImage(destination)
  const [archiving, setArchiving] = useState(false)
  const [archiveError, setArchiveError] = useState('')
  async function archive() {
    if (archiving) return
    setArchiving(true)
    setArchiveError('')
    try {
      await archiveTravelTripV2(trip.trip_id)
      onArchived()
    } catch (cause) {
      setArchiveError(cause instanceof Error ? cause.message : '归档失败，请稍后重试。')
    } finally {
      setArchiving(false)
    }
  }
  return <article className="flex items-center gap-3 rounded-[20px] border border-[#e6edf5] bg-white p-3 shadow-[0_4px_16px_rgba(34,74,122,.04)] sm:gap-4 sm:p-4"><a href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`} className="flex min-w-0 flex-1 items-center gap-3"><img src={image} alt={`${destination}行程`} loading="lazy" className="h-[76px] w-[96px] shrink-0 rounded-[14px] object-cover sm:h-[86px] sm:w-[126px]" /><span className="min-w-0 flex-1"><span className="flex items-center gap-2"><span className="truncate text-sm font-semibold text-[#20344e]">{trip.title || destination || '未命名行程'}</span><span className="rounded-full bg-[#f0f6ff] px-2 py-0.5 text-[9px] font-medium text-[#4380c9]">已保存</span></span><span className="mt-1.5 flex items-center gap-1 text-[10px] text-[#8191a6]"><CalendarDays size={12} aria-hidden />{new Date(trip.updated_at).toLocaleDateString('zh-CN')} · v{trip.revision}</span><span className="mt-1.5 block truncate text-[10px] text-[#9aa8b8]">{trip.document.brief.day_count} 天 · 点击查看行程</span></span></a><div className="flex shrink-0 flex-col items-end gap-1"><button type="button" aria-label={`归档${destination}行程`} disabled={archiving} onClick={() => void archive()} className="rounded-lg border border-[#e4ebf3] px-2.5 py-2 text-[9px] text-[#71839b] disabled:opacity-50">{archiving ? '处理中' : '删除'}</button>{archiveError && <p role="alert" className="max-w-[120px] text-right text-[8px] leading-3 text-rose-700">{archiveError}</p>}</div></article>
}

function destinationImage(destination: string): string {
  return /厦门|海|青岛|三亚/.test(destination) ? TRAVEL_PHOTOS.coast
    : /苏州|园林/.test(destination) ? TRAVEL_PHOTOS.garden
      : /杭州|西湖/.test(destination) ? TRAVEL_PHOTOS.westLake
        : TRAVEL_PHOTOS.city
}
