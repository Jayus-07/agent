'use client'

import { Plus, PanelLeftClose, PanelLeft, MessageSquare, Activity } from 'lucide-react'
import { useRouter, usePathname } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import { clsx } from 'clsx'
import SessionList from './SessionList'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const newSession = useChatStore((s) => s.newSession)
  const router = useRouter()
  const pathname = usePathname()
  const isChat = pathname === '/'
  const isMonitor = pathname?.startsWith('/monitor') ?? false

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
        <div className="mt-3 flex flex-col gap-1 w-full items-center">
          <button
            onClick={() => router.push('/')}
            className={clsx(
              'p-1.5 rounded-md transition-colors',
              isChat ? 'bg-[#2f2f2f] text-[#ececec]' : 'text-[#b4b4b4] hover:bg-[#2f2f2f]'
            )}
            aria-label="对话"
            title="对话"
          >
            <MessageSquare size={16} />
          </button>
          <button
            onClick={() => router.push('/monitor')}
            className={clsx(
              'p-1.5 rounded-md transition-colors',
              isMonitor ? 'bg-[#2f2f2f] text-[#ececec]' : 'text-[#b4b4b4] hover:bg-[#2f2f2f]'
            )}
            aria-label="可观测性"
            title="可观测性"
          >
            <Activity size={16} />
          </button>
        </div>
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

      {/* 视图切换 */}
      <div className="px-2 py-2 border-b border-[#2f2f2f]">
        <div className="flex bg-[#212121] rounded-lg p-0.5">
          <button
            onClick={() => router.push('/')}
            className={clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs rounded-md transition-colors',
              isChat
                ? 'bg-[#3a3a3a] text-[#ececec]'
                : 'text-[#8e8e8e] hover:text-[#b4b4b4]'
            )}
          >
            <MessageSquare size={13} />
            对话
          </button>
          <button
            onClick={() => router.push('/monitor')}
            className={clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs rounded-md transition-colors',
              isMonitor
                ? 'bg-[#3a3a3a] text-[#ececec]'
                : 'text-[#8e8e8e] hover:text-[#b4b4b4]'
            )}
          >
            <Activity size={13} />
            可观测性
          </button>
        </div>
      </div>

      {/* Session list (仅对话视图显示) */}
      {isChat && (
        <div className="flex-1 overflow-y-auto px-2 py-2">
          <p className="text-[11px] text-[#8e8e8e] px-2 mb-1.5 uppercase tracking-wide">历史会话</p>
          <SessionList />
        </div>
      )}
      {isMonitor && (
        <div className="flex-1 px-3 py-4 text-xs text-[#8e8e8e]">
          <p>实时监控多 Agent 工作流的执行状态、延迟分布、系统资源与告警。</p>
          <p className="mt-2 text-[#6e6e6e]">数据每 5s 自动刷新</p>
        </div>
      )}
    </aside>
  )
}
