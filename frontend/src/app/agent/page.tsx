'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Headphones } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import ChatView from '@/components/chat/ChatView'
import ChatHeader from '@/components/chat/ChatHeader'
import TaskSidebar from '@/components/agent/TaskSidebar'
import SidebarRail from '@/components/agent/SidebarRail'
import CSDrawer from '@/components/cs/CSDrawer'

export default function AgentChatPage() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [csOpen, setCsOpen] = useState(false)
  const router = useRouter()

  // 顶部标题栏：当前会话标题（store 内引用稳定字段，useMemo 派生）
  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)
  const title = useMemo(
    () => sessions.find((s) => s.id === currentId)?.title ?? '新任务',
    [sessions, currentId],
  )

  const handleNewTask = () => {
    useChatStore.getState().newSession()
    router.push('/agent')
  }

  return (
    <div className="flex-1 flex min-h-0 relative">
      {/* 左侧任务栏（任务模式：/agent 下全局控制台导航让位，见 app/layout.tsx）
          收起态渲染图标 rail：新建任务 / 展开仍可用，搜索与历史需展开 */}
      {sidebarOpen ? (
        <TaskSidebar onCollapse={() => setSidebarOpen(false)} onNewTask={handleNewTask} />
      ) : (
        <SidebarRail onExpand={() => setSidebarOpen(true)} onNewTask={handleNewTask} />
      )}

      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        <ChatHeader
          title={title}
          sidebarVisible={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onNewTask={handleNewTask}
          sessionId={currentId}
        />
        <ChatView />

        {/* 客服入口：右侧浮动按钮，点击滑出客服抽屉 */}
        {!csOpen && (
          <button
            onClick={() => setCsOpen(true)}
            title="智能客服"
            className="absolute right-5 bottom-6 z-30 flex items-center gap-2 px-4 py-2.5
              rounded-full bg-accent text-white shadow-lg
              hover:shadow-xl hover:brightness-110 transition-all"
          >
            <Headphones size={18} />
            <span className="text-sm font-medium">智能客服</span>
          </button>
        )}
      </div>

      {/* 客服抽屉（右侧滑出，见 components/cs/CSDrawer.tsx） */}
      <CSDrawer open={csOpen} onClose={() => setCsOpen(false)} />
    </div>
  )
}
