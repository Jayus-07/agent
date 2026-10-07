'use client'

/**
 * ToolProcessRows — 旅游域 Tool 事件行渲染（2026-10-03 从 page.tsx 抽出共享）：
 * 生成中卡（page）与旅行助手聊天流（TravelChatDrawer）共用同一份实现。
 * 展开口径：进行中 / 失败 / 有结果 preview 的行默认展开，其余折叠成一行摘要。
 */
import { useState } from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, Loader2, OctagonX } from 'lucide-react'
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
        // Tool Failure ≠ Workflow Failure（2026-10-07）：degraded 是「实时
        // 数据未验证但行程已继续生成」的正常降级态，呈现为琥珀色警示而非
        // 红色失败；blocked 才是硬依赖终止。
        const degradedRow = tool.status === 'degraded'
        const blockedRow = tool.status === 'blocked'
        const warningRow = degradedRow || blockedRow || failedRow
        const hasPreview = (tool.preview?.length ?? 0) > 0
        // 有结果的行默认展开——用户就是要看检索结果，
        // 折叠只能看到「N 条」数字，等于看不见（2026-10-03 实测反馈）
        const open = manualToggled.has(key)
          ? !manualDefaultOpen(running, warningRow, hasPreview)
          : manualDefaultOpen(running, warningRow, hasPreview)
        const label = TRAVEL_TOOL_LABELS[tool.tool] ?? tool.tool
        const summary = running
          ? '调用中…'
          : blockedRow
            ? (tool.userMessage || '无法验证，已停止生成')
            : degradedRow
              ? (tool.userMessage || '实时查询暂不可用 · 已继续生成行程')
              : failedRow
                ? `失败${tool.errorType ? ` · ${tool.errorType}` : ''}`
                : [
                    tool.resultCount != null ? `${tool.resultCount} 条` : '',
                    tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : '',
                  ].filter(Boolean).join(' · ') || '完成'
        const rowTone = blockedRow || failedRow
          ? 'border-red-200 bg-red-50/50'
          : degradedRow
            ? 'border-amber-200 bg-amber-50/60'
            : 'border-[#e2f0ee] bg-white'
        return (
          <li
            key={key}
            className={`overflow-hidden rounded-xl border ${rowTone}`}
          >
            <button
              type="button"
              onClick={() => toggle(key)}
              aria-expanded={open}
              className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#f5faf9]"
            >
              {running ? (
                <Loader2 size={13} className="shrink-0 animate-spin text-[#087b73]" aria-label="调用中" />
              ) : blockedRow ? (
                <OctagonX size={13} className="shrink-0 text-red-500" aria-label="已阻断" />
              ) : degradedRow ? (
                <AlertTriangle size={13} className="shrink-0 text-amber-500" aria-label="降级" />
              ) : failedRow ? (
                <AlertCircle size={13} className="shrink-0 text-red-500" aria-label="失败" />
              ) : (
                <CheckCircle2 size={13} className="shrink-0 text-[#087b73]" aria-hidden />
              )}
              <span className={`min-w-0 flex-1 truncate text-xs ${
                blockedRow || failedRow ? 'font-medium text-red-700' : degradedRow ? 'font-medium text-amber-700' : 'font-medium text-[#183037]'
              }`}>
                {label}
              </span>
              <span className={`shrink-0 text-[10px] ${
                blockedRow || failedRow ? 'text-red-600' : degradedRow ? 'text-amber-600' : 'text-[#5c7074]'
              }`}>{summary}</span>
              <ChevronDown size={12} className={`shrink-0 text-[#9db4b1] transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
            </button>
            {open && (
              <div className="border-t border-[#eef4f2] px-3 py-2.5">
                {failedRow ? (
                  <ToolFailedBody error={tool.error} errorType={tool.errorType} />
                ) : degradedRow || blockedRow ? (
                  // 降级/阻断行只给用户可读业务说明，技术细节留在 Trace
                  <p className={`text-[11px] leading-relaxed ${blockedRow ? 'text-red-700' : 'text-amber-700'}`}>
                    {tool.userMessage
                      || (degradedRow
                        ? '该实时数据源暂时不可用，已继续生成行程；相关信息未经实时验证，出行前请通过官方渠道确认。'
                        : '无法验证满足约束的实时数据，已停止生成本次行程。')}
                  </p>
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
