'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Compass, RefreshCw } from 'lucide-react'
import { fetchTravelTemplatesV2, type TravelTemplateV2 } from '@/api/travelV2'
import { PageHeading, TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import { TravelTemplateCard } from './TravelTemplateCard'

export default function TravelTemplateCatalog() {
  const [templates, setTemplates] = useState<TravelTemplateV2[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    void fetchTravelTemplatesV2().then((data) => {
      if (!cancelled) setTemplates(data.templates ?? [])
    }).catch(() => {
      if (!cancelled) {
        setTemplates([])
        setError('模板目录暂时无法加载，请检查登录状态或稍后重试。')
      }
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [reload])

  return <main className="min-h-screen bg-[#f4f8fd] pb-24 text-[#17283f] md:pb-10">
    <TravelTopBar active="discover" />
    <div className="mx-auto max-w-[1120px] px-4 pb-8 pt-2 md:px-8 md:pt-4">
      <Link href="/travel" className="mb-4 inline-flex min-h-10 items-center gap-1.5 rounded-lg pr-2 text-xs font-medium text-[#6c7d93] hover:text-[#2878f5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5]"><ArrowLeft size={14} aria-hidden />返回旅行首页</Link>
      <PageHeading eyebrow="旅行灵感" title="从精选路线开始" description="先预览平台模板；复制后会创建一份独立的正式 V2 行程。" />

      {loading ? <div role="status" aria-live="polite" className="mt-6 rounded-[22px] border border-[#e6edf5] bg-white px-4 py-12 text-center text-sm text-[#8292a6]">正在加载旅行模板…</div>
        : error ? <section role="alert" className="mt-6 rounded-[22px] border border-amber-200 bg-amber-50 p-6 text-center sm:p-8"><p className="text-sm font-semibold text-amber-900">{error}</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-4 inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-amber-300 bg-white px-4 text-xs font-semibold text-amber-900 transition hover:bg-amber-100 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600"><RefreshCw size={14} aria-hidden />重试</button></section>
          : templates.length === 0 ? <section role="status" className="mt-6 rounded-[22px] border border-dashed border-[#cfdeef] bg-white/80 px-4 py-12 text-center"><Compass size={25} className="mx-auto text-[#83a7d5]" aria-hidden /><h2 className="mt-3 text-sm font-semibold text-[#465e7a]">暂时没有可浏览的模板</h2><p className="mt-1 text-xs leading-5 text-[#8a9bb0]">可以先描述旅行想法，让 AI 帮你开始规划。</p><Link href="/travel/itineraries/new" className="mt-4 inline-flex min-h-11 items-center justify-center rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5] focus-visible:ring-offset-2">开始规划</Link></section>
            : <section aria-labelledby="travel-template-list-title" className="mt-6">
              <div className="mb-3 flex items-center justify-between gap-3"><h2 id="travel-template-list-title" className="text-sm font-semibold text-[#304761]">已加载的精选模板</h2><span className="text-[10px] text-[#8392a6]">已加载 {templates.length} 份 · 当前展示范围</span></div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3">{templates.map((template) => <TravelTemplateCard key={template.slug} template={template} />)}</div>
            </section>}
    </div>
    <TravelBottomNav active="discover" />
  </main>
}
