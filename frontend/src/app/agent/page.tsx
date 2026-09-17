'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
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

  // UX P1-⑤ 客服直达：/agent?cs=1 自动滑出客服抽屉（nav「智能客服」入口）。
  // 读 window.location 而非 useSearchParams：静态页无 Suspense 边界要求。
  // 关闭抽屉时清掉直达参数 —— 刷新不再自动弹出（打开意图已撤销）。
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('cs') === '1') {
      setCsOpen(true)
    }
  }, [])

  const handleCsClose = () => {
    setCsOpen(false)
    if (new URLSearchParams(window.location.search).has('cs')) {
      window.history.replaceState(null, '', '/agent')
    }
  }

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
          onOpenCS={() => setCsOpen(true)}
        />
        <ChatView />
      </div>

      {/* 客服抽屉（右侧滑出，见 components/cs/CSDrawer.tsx） */}
      <CSDrawer open={csOpen} onClose={handleCsClose} />
    </div>
  )
}
