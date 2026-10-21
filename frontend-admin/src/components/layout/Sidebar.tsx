'use client'

import { useEffect, useState } from 'react'
import { Sparkles, PanelLeft, PanelLeftClose } from 'lucide-react'
import NavGroup from './NavGroup'
import { visibleNav } from './navConfig'
import SidebarUserMenu from './SidebarUserMenu'
import { getRbacAccessContext } from '@/api/rbac'
import { atLeast } from '@/lib/auth'

interface Props { collapsed: boolean; onToggle: () => void }

export default function Sidebar({ collapsed, onToggle }: Props) {
  // Hydration 修复（2026-09-16）：visibleNav() 读 localStorage 角色，
  // SSR 恒为 viewer 基线 —— mount 前必须也渲染基线，保证 SSR 与客户端
  // 首帧逐字节一致；mount 后再切真实角色（admin 多出的菜单延后一帧）。
  // 否则角色差异图标（如 Database 的 <ellipse>）触发整树 hydration 失败。
  const [mounted, setMounted] = useState(false)
  const [departmentManager, setDepartmentManager] = useState(false)
  useEffect(() => {
    setMounted(true)
    if (atLeast('admin')) return undefined
    let active = true
    void getRbacAccessContext().then((context) => {
      if (active) setDepartmentManager(
        !context.isPlatformAdmin && context.managedDepartments.length > 0,
      )
    }).catch(() => undefined)
    return () => { active = false }
  }, [])
  const groups = mounted ? visibleNav(false, departmentManager) : visibleNav(true)

  if (collapsed) {
    return (
      <aside id="admin-navigation" className="w-0 shrink-0 overflow-hidden md:w-14 md:flex md:flex-col md:items-center md:py-3 md:overflow-visible glass border-r border-black/5 gap-1">
        <button type="button" onClick={onToggle} className="min-h-10 min-w-10 p-2 rounded-lg hover:bg-black/5 transition-colors text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="展开导航" aria-expanded="false" aria-controls="admin-navigation">
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
    <aside id="admin-navigation" className="w-64 shrink-0 flex flex-col glass border-r border-black/5">
      {/* 品牌 */}
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center">
            <Sparkles size={14} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-text-primary tracking-tight">电商智能数据平台</span>
        </div>
        <button type="button" onClick={onToggle} className="min-h-10 min-w-10 p-1.5 rounded-md hover:bg-black/5 transition-colors text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="收起导航" aria-expanded="true" aria-controls="admin-navigation">
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* 导航 */}
      <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-1">
        {groups.map(g => (
          <NavGroup
            key={g.path || g.label}
            {...g}
            collapsed={false}
          />
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
