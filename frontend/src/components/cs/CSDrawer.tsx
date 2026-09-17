'use client'

/**
 * CSDrawer — 用户端右侧客服抽屉
 *
 * 复用现有 CS 组件树（CSWelcome/CSMessageList/CSStatusBar/CSInput/CSHandoffCard）
 * 与 useCSChat 流式 hook，在 /agent 页右侧滑出，展示客服聊天记录。
 * 组件装配方式与管理端 frontend-admin/src/app/cs/page.tsx 保持一致。
 */
import { useCallback } from 'react'
import { Headphones, Plus, X } from 'lucide-react'
import { useCSChatStore } from '@/store/csChat'
import { useCSChat } from '@/hooks/useCSChat'
import CSWelcome from '@/components/cs/CSWelcome'
import CSMessageList from '@/components/cs/CSMessageList'
import CSInput from '@/components/cs/CSInput'
import CSStatusBar from '@/components/cs/CSStatusBar'
import CSHandoffCard from '@/components/cs/CSHandoffCard'

interface CSDrawerProps {
  open: boolean
  onClose: () => void
}

export default function CSDrawer({ open, onClose }: CSDrawerProps) {
  const { startStream, stopStream } = useCSChat()

  const currentId = useCSChatStore((s) => s.currentId)
  const messages = useCSChatStore((s) =>
    s.sessions.find((sess) => sess.id === s.currentId)?.messages ?? []
  )
  const isLoading = useCSChatStore((s) => s.isLoading)
  const error = useCSChatStore((s) => s.error)
  const currentStatus = useCSChatStore((s) => s.currentStatus)
  const intentDetected = useCSChatStore((s) => s.intentDetected)
  const handoffState = useCSChatStore((s) => s.handoffState)
  const currentNode = useCSChatStore((s) => s.currentNode)
  const setError = useCSChatStore((s) => s.setError)
  const newSession = useCSChatStore((s) => s.newSession)

  const handleSend = useCallback(
    (text: string) => {
      setError(null)
      startStream(text, currentId)
    },
    [currentId, startStream, setError]
  )

  const hasMessages = messages.length > 0

  return (
    <>
      {/* 遮罩：点击关闭 */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/20 transition-opacity"
          onClick={onClose}
        />
      )}

      {/* 右侧滑出抽屉 */}
      <div
        className={`fixed right-0 top-0 z-50 h-full w-[440px] max-w-[92vw]
          flex flex-col bg-white border-l border-border-subtle shadow-2xl
          transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border-subtle bg-white">
          <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center">
            <Headphones size={16} className="text-accent" />
          </div>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-text-primary">智能客服</h2>
            <p className="text-[10px] text-text-muted">AI 驱动的客户服务中心</p>
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => newSession()}
              title="新客服会话"
              className="flex items-center gap-1 px-2 py-1.5 text-xs rounded-lg
                border border-border-subtle text-text-secondary
                hover:text-text-primary hover:border-accent/40 transition-colors"
            >
              <Plus size={12} />
            </button>
            <button
              onClick={onClose}
              title="收起客服窗口"
              className="p-1.5 rounded-lg text-text-secondary
                hover:text-text-primary hover:bg-surface-raised transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Error toast */}
        {error && (
          <div className="mx-3 mt-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600 flex items-center justify-between">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600">
              关闭
            </button>
          </div>
        )}

        {/* Content */}
        <div className="flex-1 flex flex-col min-h-0">
          {hasMessages ? (
            <>
              <CSMessageList
                messages={messages}
                isLoading={isLoading}
                currentNode={currentNode}
              />
              {handoffState !== 'none' && <CSHandoffCard handoffState={handoffState} />}
            </>
          ) : (
            <CSWelcome onQuickPrompt={handleSend} />
          )}
        </div>

        {/* Status bar */}
        <CSStatusBar
          currentStatus={currentStatus}
          isLoading={isLoading}
          intentDetected={intentDetected}
          handoffState={handoffState}
        />

        {/* Input */}
        <CSInput onSend={handleSend} onStop={stopStream} isLoading={isLoading} />
      </div>
    </>
  )
}
