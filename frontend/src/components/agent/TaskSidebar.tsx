'use client'

/**
 * TaskSidebar — /agent 路由的左侧任务栏
 *
 * 结构（自上而下）：品牌 + 收起 / 搜索 / 新建任务 / 全部功能（折叠）/ 时间分组会话列表 / 底部用户区。
 * 全局控制台导航在 /agent 下不渲染（见 app/layout.tsx），业务入口被收进
 * 「全部功能」折叠分组，避免业务入口随改造丢失 —— 与全局 Sidebar 一样，
 * 本组件直接 map navConfig 的 NAV，故 NAV 加一项此处自动出现。
 * （2026-09-30 修正过期注释：原文「12 个业务入口」是 09-21 收敛前口径。）
 */
import { useState } from 'react'
import { Brain, ChevronDown, PanelLeftClose, Plane, Plus, RefreshCw, Search, User } from 'lucide-react'
import NavGroup from '@/components/layout/NavGroup'
import { NAV } from '@/components/layout/navConfig'
import SessionList from './SessionList'

interface Props {
  onCollapse: () => void
  onNewTask: () => void
  /**
   * travel = 旅游规划页（/travel）复用本侧栏：历史区不再渲染聊天会话列表，
   * 改由调用方经 renderHistory 注入（历史规划列表）；品牌/新建按钮文案随之切换。
   * 缺省 chat = /agent 现状，对话页零改动。
   */
  mode?: 'chat' | 'travel'
  /** mode=travel 的历史区渲染函数：拿到侧栏持有的搜索关键字与刷新信号 */
  renderHistory?: (ctx: { keyword: string; refreshKey: number; onRefreshingChange: (v: boolean) => void }) => React.ReactNode
  /** 新建按钮文案（travel 传「新建规划」） */
  newLabel?: string
  /** 搜索框占位文案（travel 传「搜索历史规划…」） */
  searchPlaceholder?: string
}

export default function TaskSidebar({
  onCollapse, onNewTask, mode = 'chat', renderHistory, newLabel = '新建任务',
  searchPlaceholder = '搜索任务…',
}: Props) {
  const [keyword, setKeyword] = useState('')
  // 12 个一级模块菜单默认展开（WorkBuddy 式布局：菜单在左上、历史在左下）；
  // 小屏可手动收起换空间。
  const [refreshKey, setRefreshKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [navOpen, setNavOpen] = useState(true)
  const isTravel = mode === 'travel'

  return (
    <aside className="hidden md:flex w-[252px] shrink-0 flex-col bg-sidebar border-r border-black/5">
      {/* 品牌 + 收起 */}
      <div className="flex items-center gap-2 px-4 h-12 shrink-0">
        {isTravel ? <Plane size={18} className="text-accent shrink-0" /> : <Brain size={18} className="text-accent shrink-0" />}
        <span className="text-sm font-semibold text-text-primary truncate">{isTravel ? '行程规划' : 'Agent AI'}</span>
        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => setRefreshKey((v) => v + 1)}
            className="p-1.5 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
            aria-label={isTravel ? '刷新历史规划' : '刷新任务列表'}
            title={isTravel ? '刷新历史规划' : '刷新任务列表'}
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
            placeholder={searchPlaceholder}
            aria-label={isTravel ? '搜索历史规划' : '搜索任务'}
            className="w-full bg-black/[0.04] border border-black/5 rounded-lg pl-7 pr-2 py-1.5 text-xs
              text-text-primary placeholder:text-text-muted outline-none focus:border-accent/40 transition-colors"
          />
        </div>
      </div>

      {/* 新建任务（主按钮，WorkBuddy 式白底描边 + 圆圈加号，不再用大色块） */}
      <div className="px-3 pb-2 shrink-0">
        <button
          onClick={onNewTask}
          className="w-full flex items-center justify-center gap-1.5 rounded-lg border border-black/10 bg-white
            px-3 py-1.5 text-[13px] font-medium text-text-primary shadow-[0_1px_2px_rgba(0,0,0,0.04)]
            hover:bg-black/[0.03] hover:border-black/15 active:scale-[0.99] transition-all duration-200"
        >
          <span className="w-4 h-4 rounded-full border border-current flex items-center justify-center shrink-0">
            <Plus size={10} strokeWidth={2.5} />
          </span>
          {newLabel}
        </button>
      </div>

      {/* 一级模块菜单（左上，默认展开；小屏可收起换空间） */}
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
              <NavGroup key={g.path || g.label} {...g} collapsed={false} compact />
            ))}
          </nav>
        )}
      </div>

      {/* 历史（左下，时间分组）——chat=会话列表；travel=调用方注入的历史规划列表 */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {isTravel && renderHistory ? (
          renderHistory({ keyword, refreshKey, onRefreshingChange: setRefreshing })
        ) : (
          <SessionList
            keyword={keyword}
            refreshKey={refreshKey}
            onRefreshingChange={setRefreshing}
            onEmptyAction={onNewTask}
          />
        )}
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
