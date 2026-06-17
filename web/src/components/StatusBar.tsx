'use client'

import { useMemo } from 'react'
import { Loader2, CheckCircle2, XCircle, Brain, Database, Library, FileText } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { parseProgress } from '@/lib/sse-parser'
import type { SSEEvent } from '@/lib/types'

const workerIcons: Record<string, React.ReactNode> = {
  sql_worker:    <Database size={13} />,
  rag_worker:    <Library size={13} />,
  report_worker: <FileText size={13} />,
  planner:       <Brain size={13} />,
}

export default function StatusBar() {
  const thinking = useChatStore((s) => s.thinking)
  const isLoading = useChatStore((s) => s.isLoading)

  // 解析 thinking 事件 → 进度信息
  const progress = useMemo(() => {
    if (!thinking.length) return null
    return parseProgress(thinking)
  }, [thinking])

  // 不显示的条件
  if (!isLoading && !progress) return null
  if (progress?.isDone) return null  // done 后自动消失

  return (
    <div className="shrink-0 border-t border-[#3f3f3f] bg-[#1a1a1a] px-4 py-2">
      <div className="max-w-3xl mx-auto flex items-center gap-3">
        {/* 左侧：当前动作 */}
        {progress?.running ? (
          <div className="flex items-center gap-2 text-sm text-[#b4b4b4] min-w-0">
            <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
            <span className="text-[#8e8e8e] shrink-0">
              {workerIcons[progress.running.node] ?? null}
            </span>
            <span className="truncate text-[#ececec]">{progress.running.description}</span>
          </div>
        ) : !progress && isLoading ? (
          <div className="flex items-center gap-2 text-sm text-[#b4b4b4]">
            <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
            <span className="text-[#ececec]">正在分析问题...</span>
          </div>
        ) : progress && !progress.isDone ? (
          <div className="flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 size={14} className="shrink-0" />
            <span>等待下一步...</span>
          </div>
        ) : null}

        {/* 右侧：进度数字 */}
        {progress && progress.total > 0 && (
          <div className="flex items-center gap-2 ml-auto text-xs text-[#8e8e8e] shrink-0">
            {progress.failed > 0 && (
              <span className="flex items-center gap-1 text-red-400">
                <XCircle size={12} />
                {progress.failed}
              </span>
            )}
            <span className={progress.completed === progress.total ? 'text-emerald-400' : ''}>
              [{progress.completed}/{progress.total}]
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
