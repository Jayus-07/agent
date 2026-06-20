'use client'

import { useChatStore } from '@/store/chat'

/**
 * 顶部宏观状态栏 — 纯 node → emoji 映射，不拼接参数。
 *
 * 数据源: store.currentStatus (由 SSE status 事件更新)
 * 映射表: store.nodeLabels  (由 SSE meta 事件下发)
 */
export default function StatusBar() {
  const currentStatus = useChatStore((s) => s.currentStatus)
  const nodeLabels = useChatStore((s) => s.nodeLabels)
  const isLoading = useChatStore((s) => s.isLoading)
  const isDone = useChatStore((s) =>
    s.streamEvents.some((e) => e.event === 'done'),
  )

  // done 后自动隐藏
  if (!isLoading && (isDone || !currentStatus)) return null

  const label = nodeLabels[currentStatus] || currentStatus || '🤔 思考中'

  return (
    <div className="shrink-0 border-t border-[#3f3f3f] bg-[#1a1a1a] px-4 py-2">
      <div className="max-w-3xl mx-auto flex items-center gap-2">
        {/* 状态标签 — 柔和过渡动效 */}
        <span
          key={currentStatus}
          className="text-sm text-[#ececec] animate-fade-in transition-opacity duration-300"
        >
          {label}
        </span>
        {/* 加载指示器 */}
        {isLoading && !isDone && (
          <span className="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
        )}
      </div>
    </div>
  )
}
