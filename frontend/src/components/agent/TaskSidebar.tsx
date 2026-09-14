'use client'

/**
 * TaskSidebar — /agent 路由的左侧任务栏
 *
 * 结构（自上而下）：品牌 + 收起 / 搜索 / 新建任务 / 全部功能（折叠）/ 时间分组会话列表 / 底部用户区。
 * 全局控制台导航在 /agent 下不渲染（见 app/layout.tsx），其 12 个业务入口被
 * 收进「全部功能」折叠分组，避免业务入口随改造丢失。
 */
import { useState } from 'react'
import { Brain, ChevronDown, PanelLeftClose, Plus, RefreshCw, Search, User } from 'lucide-react'
import NavGroup from '@/components/layout/NavGroup'
import { NAV } from '@/components/layout/navConfig'
import SessionList from './SessionList'

interface Props {
  onCollapse: () => void
  onNewTask: () => void
}

export default function TaskSidebar({ onCollapse, onNewTask }: Props) {
  const [keyword, setKeyword] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [navOpen, setNavOpen] = useState(false)

  return (
    <aside className="hidden md:flex w-[264px] shrink-0 flex-col bg-sidebar border-r border-black/5">
      {/* 品牌 + 收起 */}
      <div className="flex items-center gap-2 px-4 h-12 shrink-0">
        <Brain size={18} className="text-accent shrink-0" />
        <span className="text-sm font-semibold text-text-primary truncate">Agent AI</span>
        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => setRefreshKey((v) => v + 1)}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
            aria-label="刷新任务列表"
            title="刷新任务列表"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onCollapse}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
            aria-label="收起任务栏"
            title="收起任务栏"
          >
            <PanelLeftClose size={15} />
          </button>
        </div>
      </div>

      {/* 搜索 */}
      <div className="px-3 pb-2 shrink-0">
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索任务…"
            aria-label="搜索任务"
            className="w-full bg-black/[0.04] border border-black/5 rounded-lg pl-7 pr-2 py-1.5 text-xs
              text-text-primary placeholder:text-text-muted outline-none focus:border-accent/40 transition-colors"
          />
        </div>
      </div>

      {/* 新建任务（主按钮） */}
      <div className="px-3 pb-2 shrink-0">
        <button
          onClick={onNewTask}
          className="w-full flex items-center justify-center gap-1.5 rounded-xl bg-accent px-3 py-2
            text-[13px] font-medium text-white shadow-sm
            hover:bg-accent-hover active:scale-[0.99] transition-all duration-200"
        >
          <Plus size={14} />
          新建任务
        </button>
      </div>

      {/* 全部功能（收纳原 NAV 12 个业务模块，默认折叠） */}
      <div className="px-3 pb-1 shrink-0">
        <button
          onClick={() => setNavOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] text-text-secondary
            hover:text-text-primary hover:bg-black/5 transition-colors"
          aria-expanded={navOpen}
        >
          <span className="flex-1 text-left truncate font-medium">全部功能</span>
          <ChevronDown
            size={14}
            className={`shrink-0 transition-transform duration-200 ${navOpen ? 'rotate-180' : ''}`}
          />
        </button>
        {navOpen && (
          <nav className="mt-0.5 space-y-0.5 max-h-[45vh] overflow-y-auto">
            {NAV.map((g) => (
              <NavGroup key={g.path || g.label} {...g} collapsed={false} />
            ))}
          </nav>
        )}
      </div>

      {/* 任务列表（时间分组） */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        <SessionList
          keyword={keyword}
          refreshKey={refreshKey}
          onRefreshingChange={setRefreshing}
          onEmptyAction={onNewTask}
        />
      </div>

      {/* 底部用户区 */}
      <div className="shrink-0 border-t border-black/5 px-3 py-3 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center shrink-0">
          <User size={15} className="text-accent" />
        </div>
        <div className="min-w-0">
          <div className="text-[13px] font-medium text-text-primary truncate">本地用户</div>
          <div className="text-[10px] text-text-muted truncate">电商 RAG 工作台</div>
        </div>
      </div>
    </aside>
  )
}
