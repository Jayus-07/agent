'use client'

import type { ReactNode } from 'react'

/**
 * ChatHeader — 任务模式顶部标题栏（h-14）
 * 左：任务栏开关 + 当前会话标题；右：门户入口 + 统一个人侧栏。
 * 模型切换已下移到输入框工具栏（ComposerToolbar），此处不再重复。
 * 全局控制台导航在 /agent 下不渲染（见 app/layout.tsx）。
 */
import { PanelLeft, PanelLeftClose, Plus } from 'lucide-react'
import MobileAssistantDrawer from '@/components/layout/MobileAssistantDrawer'

interface Props {
  title: string
  sidebarVisible: boolean
  onToggleSidebar: () => void
  onNewTask?: () => void
  /** 移动端头像侧栏中展示的历史会话列表；桌面使用左侧任务栏。 */
  mobileHistoryContent?: (onSelectSession: () => void) => ReactNode
}

export default function ChatHeader({
  title, sidebarVisible, onToggleSidebar, onNewTask, mobileHistoryContent,
}: Props) {

  return (
    <header className="shrink-0 h-14 flex items-center gap-2 px-4 bg-surface-base/80">
      {/* 手机上左侧新建会话；历史列表收进头像侧栏。 */}
      {onNewTask && (
        <button
          onClick={onNewTask}
          className="md:hidden shrink-0 flex h-9 w-9 items-center justify-center rounded-lg text-text-secondary
            hover:text-text-primary hover:bg-black/5 transition-colors"
          aria-label="新建会话"
          title="新建会话"
        >
          <Plus size={18} />
        </button>
      )}

      {/* 任务栏开关（桌面；窄视口下任务栏本就隐藏，开关无意义） */}
      <button
        onClick={onToggleSidebar}
        className="hidden md:block p-1.5 rounded-lg hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
        aria-label={sidebarVisible ? '收起任务栏' : '展开任务栏'}
        title={sidebarVisible ? '收起任务栏' : '展开任务栏'}
      >
        {sidebarVisible ? <PanelLeftClose size={17} /> : <PanelLeft size={17} />}
      </button>

      <h1 className="flex-1 min-w-0 truncate text-[15px] font-semibold text-text-primary" title={title}>
        {title}
      </h1>

      <MobileAssistantDrawer historyContent={mobileHistoryContent} />
    </header>
  )
}
