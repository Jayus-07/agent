'use client'

/**
 * FileOpsCard — 工具产出文件清单（P1：file 事件渲染）
 *
 * 数据源：store.fileOps —— 后端在 skill 步骤落盘文件（如 export_csv）
 * 时下发的 file 事件，按路径去重。无记录时不渲染。
 * 与 TodoCard 同区：任务执行 → 产出物的完整链路。
 */

import { FileText } from 'lucide-react'
import { useChatStore } from '@/store/chat'

export default function FileOpsCard() {
  const fileOps = useChatStore((s) => s.fileOps)
  if (fileOps.length === 0) return null

  return (
    <div className="border border-border-subtle rounded-xl bg-surface-elevated overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <div className="flex items-center gap-2 text-xs font-medium text-text-primary">
          <FileText size={13} className="text-accent" />
          产出文件
        </div>
        <span className="text-[10px] text-text-muted tabular-nums">{fileOps.length} 个</span>
      </div>
      <div className="px-4 py-2" role="list" aria-label="工具产出文件">
        {fileOps.map((op) => (
          <div key={op.path} role="listitem" className="flex items-center gap-2 py-0.5 min-w-0"
            title={`${op.node} · ${op.step_id}`}>
            <span className="w-1.5 h-1.5 rounded-sm bg-green-500 shrink-0" aria-hidden />
            <span className="text-[11px] text-text-secondary truncate font-mono">
              {op.path}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
