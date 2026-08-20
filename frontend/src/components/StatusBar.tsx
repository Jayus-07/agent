'use client'

import { useChatStore } from '@/store/chat'

export default function StatusBar() {
  const currentStatus = useChatStore((s) => s.currentStatus)
  const nodeLabels = useChatStore((s) => s.nodeLabels)
  const isLoading = useChatStore((s) => s.isLoading)
  const isDone = useChatStore((s) => s.streamEvents.some((e) => e.event === 'done'))

  // 非加载中一律隐藏：网络异常 catch 路径没有终态事件，
  // currentStatus 可能残留旧值，仅靠 store 清理不保险
  if (!isLoading || isDone) return null

  const label = nodeLabels[currentStatus] || currentStatus || '思考中'

  return (
    <div className="shrink-0 px-5 py-1.5">
      <div className="max-w-[720px] mx-auto flex items-center gap-2">
        <span key={currentStatus} className="text-xs text-text-muted animate-fade-in">
          {label}
        </span>
        {isLoading && !isDone && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent/50 animate-pulse" />
        )}
      </div>
    </div>
  )
}
