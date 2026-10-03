'use client'

/**
 * ToolProcessRows — 旅游域 Tool 事件行渲染（2026-10-03 从 page.tsx 抽出共享）：
 * 生成中卡（page）与旅行助手聊天流（TravelChatDrawer）共用同一份实现。
 * 展开口径：进行中 / 失败 / 有结果 preview 的行默认展开，其余折叠成一行摘要。
 */
import { useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, Loader2 } from 'lucide-react'
import { TRAVEL_TOOL_LABELS } from './travelDisplay'
import type { TravelProcessTool } from './travelRuntime'
import { ToolFailedBody, ToolPreviewBody } from './ToolPreviews'

function manualDefaultOpen(running: boolean, failedRow: boolean, hasPreview: boolean): boolean {
  return running || failedRow || hasPreview
}

export default function ToolProcessRows({ tools, onAsk }: {
  tools: TravelProcessTool[]
  /** M2 代发：透传给商户卡「帮我排进行程」按钮 */
  onAsk?: (text: string) => void
}) {
  const [manualToggled, setManualToggled] = useState<Set<string>>(new Set())
  const toggle = (key: string) => {
    setManualToggled((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }
  return (
    <>
      {tools.map((tool, index) => {
        const key = `tool-${index}`
        const running = tool.status === 'running'
        const failedRow = tool.status === 'failed'
        const hasPreview = (tool.preview?.length ?? 0) > 0
        // 有结果的行默认展开——用户就是要看检索结果，
        // 折叠只能看到「N 条」数字，等于看不见（2026-10-03 实测反馈）
        const open = manualToggled.has(key)
          ? !manualDefaultOpen(running, failedRow, hasPreview)
          : manualDefaultOpen(running, failedRow, hasPreview)
        const label = TRAVEL_TOOL_LABELS[tool.tool] ?? tool.tool
        const summary = running
          ? '调用中…'
          : failedRow
            ? `失败${tool.errorType ? ` · ${tool.errorType}` : ''}`
            : [
                tool.resultCount != null ? `${tool.resultCount} 条` : '',
                tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : '',
              ].filter(Boolean).join(' · ') || '完成'
        return (
          <li
            key={key}
            className={`overflow-hidden rounded-xl border ${
              failedRow ? 'border-red-200 bg-red-50/50' : 'border-[#e2f0ee] bg-white'
            }`}
          >
            <button
              type="button"
              onClick={() => toggle(key)}
              aria-expanded={open}
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#f5faf9]"
            >
              {running ? (
                <Loader2 size={13} className="shrink-0 animate-spin text-[#087b73]" aria-label="调用中" />
              ) : failedRow ? (
                <AlertCircle size={13} className="shrink-0 text-red-500" aria-label="失败" />
              ) : (
                <CheckCircle2 size={13} className="shrink-0 text-[#087b73]" aria-hidden />
              )}
              <span className={`min-w-0 flex-1 truncate text-xs ${failedRow ? 'font-medium text-red-700' : 'font-medium text-[#183037]'}`}>
                {label}
              </span>
              <span className={`shrink-0 text-[10px] ${failedRow ? 'text-red-600' : 'text-[#5c7074]'}`}>{summary}</span>
              <ChevronDown size={12} className={`shrink-0 text-[#9db4b1] transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
            </button>
            {open && (
              <div className="border-t border-[#eef4f2] px-3 py-2.5">
                {failedRow ? (
                  <ToolFailedBody error={tool.error} errorType={tool.errorType} />
                ) : hasPreview ? (
                  <ToolPreviewBody preview={tool.preview!} category={tool.category} onAsk={onAsk} />
                ) : running ? (
                  <p className="text-[11px] text-[#7a8e8b]">正在调用真实数据源，结果返回后自动展开…</p>
                ) : (
                  <p className="text-[11px] text-[#7a8e8b]">
                    {tool.dataStatus === 'empty'
                      ? '查询成功，当前没有匹配结果。'
                      : tool.dataStatus === 'unavailable'
                        ? '数据源暂不可用，已按降级口径继续规划。'
                        : '执行完成。'}
                  </p>
                )}
              </div>
            )}
          </li>
        )
      })}
    </>
  )
}
