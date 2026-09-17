'use client'

import { Sparkles, PanelLeft, PanelLeftClose } from 'lucide-react'
import NavGroup from './NavGroup'
import { NAV } from './navConfig'

interface Props { collapsed: boolean; onToggle: () => void }

export default function Sidebar({ collapsed, onToggle }: Props) {
  if (collapsed) {
    return (
      <aside className="w-0 shrink-0 overflow-hidden md:w-14 md:flex md:flex-col md:items-center md:py-3 glass border-r border-black/5 gap-1">
        <button onClick={onToggle} className="p-2 rounded-lg hover:bg-black/5 transition-colors text-text-secondary" aria-label="展开">
          <PanelLeft size={18} />
        </button>
        {NAV.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={true} />
        ))}
      </aside>
    )
  }

  return (
    <aside className="w-64 shrink-0 hidden md:flex flex-col glass border-r border-black/5">
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
        {NAV.map(g => (
          <NavGroup key={g.path || g.label} {...g} collapsed={false} />
        ))}
      </nav>

      {/* 底部 */}
      <div className="px-4 py-3 border-t border-black/5">
        <p className="text-[10px] text-text-muted leading-relaxed">
          Powered by LangGraph<br />Multi-Agent System
        </p>
      </div>
    </aside>
  )
}
