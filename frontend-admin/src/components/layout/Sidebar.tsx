'use client'

import { useEffect, useState } from 'react'
import { Sparkles, PanelLeft, PanelLeftClose } from 'lucide-react'
import NavGroup from './NavGroup'
import { visibleNav } from './navConfig'
import SidebarUserMenu from './SidebarUserMenu'

interface Props { collapsed: boolean; onToggle: () => void }

export default function Sidebar({ collapsed, onToggle }: Props) {
  // Hydration 修复（2026-09-16）：visibleNav() 读 localStorage 角色，
  // SSR 恒为 viewer 基线 —— mount 前必须也渲染基线，保证 SSR 与客户端
  // 首帧逐字节一致；mount 后再切真实角色（admin 多出的菜单延后一帧）。
  // 否则角色差异图标（如 Database 的 <ellipse>）触发整树 hydration 失败。
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const groups = mounted ? visibleNav() : visibleNav(true)

  if (collapsed) {
    return (
      <aside className="w-0 shrink-0 overflow-visible md:w-14 md:flex md:flex-col md:items-center md:py-3 glass border-r border-black/5 gap-1">
        <button onClick={onToggle} className="p-2 rounded-lg hover:bg-black/5 transition-colors text-text-secondary" aria-label="展开">
          <PanelLeft size={18} />
        </button>
        {groups.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={true} />
        ))}
        <SidebarUserMenu collapsed />
      </aside>
    )
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col glass border-r border-black/5">
      {/* 品牌 */}
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center">
            <Sparkles size={14} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-text-primary tracking-tight">电商智能数据平台</span>
        </div>
        <button onClick={onToggle} className="p-1.5 rounded-md hover:bg-black/5 transition-colors text-text-secondary" aria-label="收起">
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* 导航 */}
      <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
        {groups.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={false} />
        ))}
      </nav>

      {/* 底部 */}
      <SidebarUserMenu />
      <div className="px-4 py-3 border-t border-black/5">
        <p className="text-[10px] text-text-muted leading-relaxed">
          Powered by LangGraph<br />Multi-Agent System
        </p>
      </div>
    </aside>
  )
}
