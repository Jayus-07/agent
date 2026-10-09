'use client'

import type { ReactNode } from 'react'
import { CalendarDays, CircleUserRound, Compass, Info, Plane, Sparkles } from 'lucide-react'

export type TravelNavItem = 'home' | 'trips' | 'me'

export function TravelTopBar({ active = 'home' }: { active?: TravelNavItem }) {
  return (
    <header className="mx-auto flex w-full max-w-[1440px] items-center justify-between px-4 py-3 md:px-8 md:py-4">
      <a href="/travel" className="flex items-center gap-2.5 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2878f5]">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#2878f5] text-white shadow-[0_6px_18px_rgba(40,120,245,.2)]">
          <Plane size={18} strokeWidth={2.4} aria-hidden />
        </span>
        <span>
          <span className="block text-sm font-bold tracking-tight text-[#17283f]">漫游</span>
          <span className="hidden text-[10px] text-[#8190a4] sm:block">AI 旅行助手</span>
        </span>
      </a>
      <nav aria-label="旅游导航" className="hidden items-center gap-1 md:flex">
        <a aria-current={active === 'home' ? 'page' : undefined} href="/travel" className={`rounded-full px-4 py-2 text-sm transition ${active === 'home' ? 'bg-[#eaf2ff] font-semibold text-[#236bd5]' : 'text-[#65758b] hover:bg-white'}`}>首页</a>
        <a aria-current={active === 'trips' ? 'page' : undefined} href="/travel/itineraries" className={`rounded-full px-4 py-2 text-sm transition ${active === 'trips' ? 'bg-[#eaf2ff] font-semibold text-[#236bd5]' : 'text-[#65758b] hover:bg-white'}`}>我的行程</a>
        <a href="/travel/templates/hangzhou-slow" className="rounded-full px-4 py-2 text-sm text-[#65758b] transition hover:bg-white">旅行灵感</a>
      </nav>
      <a href="/settings" className="hidden items-center gap-2 rounded-full border border-[#e4ebf4] bg-white px-3 py-2 text-xs font-medium text-[#53657c] shadow-sm md:flex">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#eaf2ff] text-[#2878f5]">林</span>
        我的
      </a>
      <span className="md:hidden rounded-full border border-[#dbe8fb] bg-white px-2.5 py-1 text-[10px] font-medium text-[#2878f5]">旅行灵感</span>
    </header>
  )
}

export function DemoNotice({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`flex items-start gap-2 rounded-xl border border-[#d9e8ff] bg-[#f1f7ff] text-[#49688e] ${compact ? 'px-2.5 py-2 text-[10px]' : 'px-3 py-2.5 text-xs'}`} role="status">
      <Info size={compact ? 13 : 15} className="mt-0.5 shrink-0 text-[#2878f5]" aria-hidden />
      <span><strong className="font-semibold text-[#2866b1]">演示模式</strong> · 页面使用示例行程，修改仅在当前页面展示，不会保存到账号。</span>
    </div>
  )
}

export function TravelBottomNav({ active }: { active: TravelNavItem }) {
  const items = [
    { key: 'home' as const, label: '首页', href: '/travel', icon: Compass },
    { key: 'trips' as const, label: '行程', href: '/travel/itineraries', icon: CalendarDays },
    { key: 'me' as const, label: '我的', href: '/settings', icon: CircleUserRound },
  ]
  return (
    <nav aria-label="手机导航" className="fixed inset-x-0 bottom-0 z-30 grid h-[68px] grid-cols-3 border-t border-[#e7edf5] bg-white/95 px-3 pb-[env(safe-area-inset-bottom)] shadow-[0_-6px_22px_rgba(30,64,110,.06)] backdrop-blur md:hidden">
      {items.map(({ key, label, href, icon: Icon }) => (
        <a key={key} href={href} aria-current={active === key ? 'page' : undefined} className={`flex flex-col items-center justify-center gap-1 text-[10px] ${active === key ? 'font-semibold text-[#2878f5]' : 'text-[#8895a6]'}`}>
          <Icon size={19} strokeWidth={active === key ? 2.5 : 1.8} aria-hidden />
          {label}
        </a>
      ))}
    </nav>
  )
}

export function PageHeading({ eyebrow, title, description, trailing }: {
  eyebrow?: string
  title: string
  description?: string
  trailing?: ReactNode
}) {
  return (
    <div className="flex items-end justify-between gap-3">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[#5282bd]"><Sparkles size={13} aria-hidden />{eyebrow}</div>}
        <h1 className="text-[25px] font-bold leading-tight tracking-[-.035em] text-[#17283f] md:text-[32px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm leading-6 text-[#718198]">{description}</p>}
      </div>
      {trailing}
    </div>
  )
}
