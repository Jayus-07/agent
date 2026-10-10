'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/store/chat'
import ChatView from '@/components/chat/ChatView'
import ChatHeader from '@/components/chat/ChatHeader'
import TaskSidebar from '@/components/agent/TaskSidebar'
import SidebarRail from '@/components/agent/SidebarRail'
import SessionList from '@/components/agent/SessionList'

export default function AgentChatPage() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const router = useRouter()

  // 旧客服抽屉直达链接兼容到独立客服页。
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('cs') === '1') {
      router.replace('/customer-service')
    }
  }, [router])

  // HandoffCard 的预填问题沿用 sessionStorage 交接，事件现在导航到客服独立页。
  useEffect(() => {
    const open = () => router.push('/customer-service')
    window.addEventListener('cs-drawer:open', open)
    return () => window.removeEventListener('cs-drawer:open', open)
  }, [router])

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
          mobileHistoryContent={(onSelectSession) => (
            <SessionList
              onEmptyAction={() => {
                onSelectSession()
                handleNewTask()
              }}
              onSessionSelect={onSelectSession}
            />
          )}
        />
        <ChatView />
      </div>
    </div>
  )
}
