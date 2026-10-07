'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { History } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import ChatView from '@/components/chat/ChatView'
import ChatHeader from '@/components/chat/ChatHeader'
import TaskSidebar from '@/components/agent/TaskSidebar'
import SidebarRail from '@/components/agent/SidebarRail'
import SessionList from '@/components/agent/SessionList'
import CSDrawer from '@/components/cs/CSDrawer'

export default function AgentChatPage() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [csOpen, setCsOpen] = useState(false)
  // 移动端（<md 无任务栏）历史会话浮层：与 /travel 历史规划浮层同一交互形态
  const [historyOpen, setHistoryOpen] = useState(false)
  const router = useRouter()

  // UX P1-⑤ 客服直达：/agent?cs=1 自动滑出客服抽屉（nav「智能客服」入口）。
  // 读 window.location 而非 useSearchParams：静态页无 Suspense 边界要求。
  // 关闭抽屉时清掉直达参数 —— 刷新不再自动弹出（打开意图已撤销）。
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('cs') === '1') {
      setCsOpen(true)
    }
  }, [])

  // 多域隔离 M3（2026-10-06）：主图引导卡（HandoffCard）客服跳转入口 ——
  // 同页事件滑出抽屉（?cs=1 的 useEffect 只在挂载时跑一次，同页 push 不触发）
  useEffect(() => {
    const open = () => setCsOpen(true)
    window.addEventListener('cs-drawer:open', open)
    return () => window.removeEventListener('cs-drawer:open', open)
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

  // 选中会话（currentId 变化）后自动收起历史浮层——SessionList 内部直接 router.push，
  // 同路由客户端导航不会卸载本页，必须靠 currentId 变化关浮层
  useEffect(() => {
    setHistoryOpen(false)
  }, [currentId])

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
          onOpenHistory={() => setHistoryOpen(true)}
        />
        <ChatView />
      </div>

      {/* 客服抽屉（右侧滑出，见 components/cs/CSDrawer.tsx） */}
      <CSDrawer open={csOpen} onClose={handleCsClose} />

      {/* 移动端（<md 无任务栏）历史会话浮层：与桌面任务栏同一数据组件 SessionList */}
      <AgentHistorySheet
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onNewTask={handleNewTask}
      />
    </div>
  )
}

/** 历史会话浮层（窄屏 <md）：照抄 /travel 历史规划浮层的交互形态 */
function AgentHistorySheet({
  open, onClose, onNewTask,
}: {
  open: boolean
  onClose: () => void
  onNewTask: () => void
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-40 bg-black/20 md:hidden"
      role="presentation"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-[320px] max-w-[88vw] flex-col border-r border-black/5 bg-white shadow-2xl"
        aria-label="历史会话"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-black/5 px-4 py-3">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-text-primary">
            <History size={14} className="text-accent" aria-hidden />
            历史会话
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-xs text-text-secondary hover:bg-black/5"
          >
            关闭
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {/* SessionList 选中态走 router.push；这里经 onEmptyAction 引导新建。
              选中跳转会整页导航到 /agent?session=...，浮层随页面刷新自然关闭 */}
          <SessionList onEmptyAction={onNewTask} />
        </div>
      </aside>
    </div>
  )
}
