'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import ChatView from '@/components/ChatView'
import ChatHeader from '@/components/chat/ChatHeader'
import TaskSidebar from '@/components/agent/TaskSidebar'

export default function AgentChatPage() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
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
      {/* 左侧任务栏（任务模式：/agent 下全局控制台导航让位，见 app/layout.tsx） */}
      {sidebarOpen && (
        <TaskSidebar onCollapse={() => setSidebarOpen(false)} onNewTask={handleNewTask} />
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
      </div>
    </div>
  )
}
