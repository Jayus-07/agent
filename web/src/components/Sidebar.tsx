'use client'

import { Plus, PanelLeftClose, PanelLeft } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import SessionList from './SessionList'
import ModeTabs from './ModeTabs'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const newSession = useChatStore((s) => s.newSession)

  if (collapsed) {
    return (
      <aside className="w-0 shrink-0 overflow-hidden md:w-12 md:flex md:flex-col md:items-center md:py-3 md:border-r md:border-[#3f3f3f] bg-[#171717]">
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
          aria-label="展开侧边栏"
        >
          <PanelLeft size={18} />
        </button>
      </aside>
    )
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col bg-[#171717] border-r border-[#3f3f3f]">
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-[#2f2f2f]">
        <span className="text-sm font-semibold tracking-wide text-[#ececec]">Agent AI</span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => newSession()}
            className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
            aria-label="新对话"
            title="新对话"
          >
            <Plus size={16} />
          </button>
          <button
            onClick={onToggle}
            className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
            aria-label="收起侧边栏"
            title="收起"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
      </div>

      {/* 模式选择 */}
      <ModeTabs />

      {/* 分隔线 */}
      <div className="h-px bg-[#2f2f2f] mx-3" />

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <p className="text-[11px] text-[#8e8e8e] px-2 mb-1.5 uppercase tracking-wide">历史会话</p>
        <SessionList />
      </div>
    </aside>
  )
}
