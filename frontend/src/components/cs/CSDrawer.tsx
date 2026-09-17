'use client'

/**
 * CSDrawer — 用户端右侧客服抽屉
 *
 * 复用现有 CS 组件树（CSWelcome/CSMessageList/CSStatusBar/CSInput/CSHandoffCard）
 * 与 useCSChat 流式 hook，在 /agent 页右侧滑出，展示客服聊天记录。
 * 组件装配方式与管理端 frontend-admin/src/app/cs/page.tsx 保持一致。
 */
import { useCallback, useEffect, useState } from 'react'
import { Headphones, Plus, X } from 'lucide-react'
import { useCSChatStore } from '@/store/csChat'
import { useCSChat } from '@/hooks/useCSChat'
import { useCSHandoffSync } from '@/hooks/useCSHandoffSync'
import CSWelcome from '@/components/cs/CSWelcome'
import CSMessageList from '@/components/cs/CSMessageList'
import CSInput from '@/components/cs/CSInput'
import CSStatusBar from '@/components/cs/CSStatusBar'
import CSHandoffCard from '@/components/cs/CSHandoffCard'
import CSSatisfactionCard from '@/components/cs/CSSatisfactionCard'

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

  // 人工介入同步：坐席消息轮询入列 + 转接状态卡片（抽屉打开期间生效）
  useCSHandoffSync(currentId, open)

  // 满意度评价：会话有回复且非流式中显示；切换会话时重置
  const [showSatisfaction, setShowSatisfaction] = useState(true)
  useEffect(() => {
    setShowSatisfaction(true)
  }, [currentId])
  const lastRole = messages.length > 0 ? messages[messages.length - 1].role : null
  const showRatingCard = messages.length > 0 && !isLoading && showSatisfaction
    && (lastRole === 'assistant' || lastRole === 'agent')

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

      {/* 右侧滑出抽屉 —— ASSISTANT UNAWARE
          宽度：桌面固定 440px，窄屏撑满可用宽度（原 max-w-[92vw] 会留出 8vw 的无用边缝，
          遮罩下露出主界面且在 1024px 级屏幕上浪费近 80px 内容宽度） */}
      <div
        className={`fixed right-0 top-0 z-50 h-full w-[440px] max-sm:w-full
          flex flex-col bg-white border-l border-border-subtle shadow-2xl
          transition-transform duration-300 ease-out
          ${open ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="shrink-0 flex items-center gap-2.5 px-4 py-3 border-b border-border-subtle bg-white">
          <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
            <Headphones size={17} className="text-accent" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-text-primary truncate">智能客服</h2>
            <p className="text-[10px] text-text-muted truncate">AI 驱动的客户服务中心</p>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => newSession()}
              title="新客服会话"
              aria-label="新客服会话"
              className="flex items-center justify-center w-7 h-7 rounded-lg
                border border-border-subtle text-text-secondary
                hover:text-text-primary hover:border-accent/40 transition-colors"
            >
              <Plus size={14} />
            </button>
            <button
              onClick={onClose}
              title="收起客服窗口"
              aria-label="收起客服窗口"
              className="flex items-center justify-center w-7 h-7 rounded-lg text-text-secondary
                hover:text-text-primary hover:bg-surface-raised transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Error toast */}
        {error && (
          <div className="shrink-0 mx-3 mt-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600 flex items-center gap-2">
            <span className="flex-1 min-w-0 break-words">{error}</span>
            <button onClick={() => setError(null)} className="shrink-0 text-red-400 hover:text-red-600">
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
              {showRatingCard && (
                <CSSatisfactionCard
                  conversationId={currentId}
                  onDismissed={() => setShowSatisfaction(false)}
                />
              )}
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
