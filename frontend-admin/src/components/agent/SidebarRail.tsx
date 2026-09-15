'use client'

/**
 * SidebarRail — 任务侧边栏收起后的图标 rail（WorkBuddy 式收起态）
 *
 * 收起 ≠ 消失：保留品牌入口（点击展开）、新建任务、展开按钮三项主要操作，
 * 搜索/历史等重内容仍需展开侧边栏使用。
 * 仅桌面端渲染（<md 视口下任务栏本就隐藏，见 TaskSidebar / ChatHeader 同款断点）。
 */
import { Brain, PanelLeftOpen, Plus } from 'lucide-react'

interface Props {
  onExpand: () => void
  onNewTask: () => void
}

export default function SidebarRail({ onExpand, onNewTask }: Props) {
  return (
    <aside
      className="hidden md:flex w-12 shrink-0 flex-col items-center gap-1 py-3
        bg-sidebar border-r border-black/5"
      aria-label="任务栏（收起态）"
    >
      {/* 品牌 = 展开入口 */}
      <button
        onClick={onExpand}
        className="p-2 rounded-lg hover:bg-black/5 transition-colors"
        aria-label="展开任务栏"
        title="展开任务栏"
      >
        <Brain size={18} className="text-accent" />
      </button>

      {/* 新建任务 */}
      <button
        onClick={onNewTask}
        className="p-2 rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
        aria-label="新建任务"
        title="新建任务"
      >
        <Plus size={16} />
      </button>

      <div className="flex-1" />

      {/* 展开 */}
      <button
        onClick={onExpand}
        className="p-2 rounded-lg text-text-muted hover:text-text-primary hover:bg-black/5 transition-colors"
        aria-label="展开任务栏"
        title="展开任务栏"
      >
        <PanelLeftOpen size={16} />
      </button>
    </aside>
  )
}
