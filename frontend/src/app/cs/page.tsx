'use client'

import { useCallback } from 'react'
import { Headphones, Plus } from 'lucide-react'
import { useCSChatStore } from '@/store/csChat'
import { useCSChat } from '@/hooks/useCSChat'
import CSWelcome from '@/components/cs/CSWelcome'
import CSMessageList from '@/components/cs/CSMessageList'
import CSInput from '@/components/cs/CSInput'
import CSStatusBar from '@/components/cs/CSStatusBar'
import CSHandoffCard from '@/components/cs/CSHandoffCard'

export default function CustomerServicePage() {
  const { startStream, stopStream } = useCSChat()

  const sessions = useCSChatStore((s) => s.sessions)
  const currentId = useCSChatStore((s) => s.currentId)
  const messages = useCSChatStore((s) =>
    s.sessions.find((sess) => sess.id === s.currentId)?.messages ?? []
  )
  const isLoading = useCSChatStore((s) => s.isLoading)
  const error = useCSChatStore((s) => s.error)
  const currentStatus = useCSChatStore((s) => s.currentStatus)
  const deltaText = useCSChatStore((s) => s.deltaText)
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

  const handleNewSession = useCallback(() => {
    newSession()
  }, [newSession])

  const hasMessages = messages.length > 0

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-bg-root">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-3 border-b border-border-subtle bg-surface-base">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center">
            <Headphones size={18} className="text-accent" />
          </div>
          <div>
            <h1 className="text-sm font-semibold text-text-primary">智能客服</h1>
            <p className="text-[10px] text-text-muted">AI 驱动的客户服务中心</p>
          </div>
        </div>
        <button
          onClick={handleNewSession}
          className="ml-auto flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-lg
            border border-border-subtle text-text-secondary
            hover:text-text-primary hover:border-accent/40 transition-colors"
        >
          <Plus size={12} />
          新会话
        </button>
      </div>

      {/* Error toast */}
      {error && (
        <div className="mx-4 mt-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600">
            关闭
          </button>
        </div>
      )}

      {/* Content */}
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
  )
}
