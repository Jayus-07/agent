'use client'

import { useState, useEffect, useRef, useMemo } from 'react'
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, AlertTriangle, Info, Circle } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import type { SSEStreamEvent, LogEvent } from '@/lib/types'

/** 从 streamEvents 中提取所有 log 事件 */
function extractLogs(events: SSEStreamEvent[]): (LogEvent & { ts: number })[] {
  return events
    .filter((e): e is { event: 'log'; data: LogEvent } => e.event === 'log')
    .map((e) => ({ ...e.data, ts: e.data.ts }))
}

/** level → 图标 + 颜色 */
function logStyle(level: LogEvent['level']) {
  switch (level) {
    case 'error':
      return { icon: <XCircle size={14} className="text-red-400 shrink-0 mt-0.5" />, color: 'text-red-400', bg: 'bg-red-400/10' }
    case 'warn':
      return { icon: <AlertTriangle size={14} className="text-amber-400 shrink-0 mt-0.5" />, color: 'text-amber-400', bg: 'bg-amber-400/10' }
    default:
      return { icon: <Info size={14} className="text-[#8e8e8e] shrink-0 mt-0.5" />, color: 'text-[#8e8e8e]', bg: '' }
  }
}

export default function ThinkingPanel() {
  const streamEvents = useChatStore((s) => s.streamEvents)
  const nodeLabels = useChatStore((s) => s.nodeLabels)
  const isLoading = useChatStore((s) => s.isLoading)
  const [collapsed, setCollapsed] = useState(true)   // 默认折叠
  const [showBadge, setShowBadge] = useState(false)   // 红点闪烁

  const logs = useMemo(() => extractLogs(streamEvents), [streamEvents])
  const prevLogLen = useRef(0)
  const badgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 新日志到达 → 红点闪烁（3s 或展开后消失）
  useEffect(() => {
    if (logs.length > prevLogLen.current && collapsed) {
      setShowBadge(true)
      if (badgeTimer.current) clearTimeout(badgeTimer.current)
      badgeTimer.current = setTimeout(() => setShowBadge(false), 3000)
    }
    prevLogLen.current = logs.length
    return () => {
      if (badgeTimer.current) clearTimeout(badgeTimer.current)
    }
  }, [logs.length, collapsed])

  // 展开面板 → 消除红点
  const handleToggle = () => {
    setCollapsed((v) => {
      if (v) setShowBadge(false)  // 展开 → 消红点
      return !v
    })
  }

  if (logs.length === 0 && !isLoading) return null

  const errorCount = logs.filter((l) => l.level === 'error').length
  const warnCount = logs.filter((l) => l.level === 'warn').length
  const hasError = errorCount > 0

  return (
    <div className="mb-4 border border-[#3f3f3f] rounded-xl overflow-hidden bg-[#1a1a1a]">
      {/* 折叠按钮 */}
      <button
        onClick={handleToggle}
        className={`w-full flex items-center gap-2 px-4 py-2.5 text-xs font-medium transition-colors relative ${
          hasError ? 'text-red-400' : 'text-[#b4b4b4]'
        }`}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span>思维链日志</span>
        <span className="text-[#8e8e8e] font-normal">({logs.length} 条)</span>
        {errorCount > 0 && (
          <span className="text-red-400 font-normal">⚠ {errorCount}</span>
        )}
        {warnCount > 0 && (
          <span className="text-amber-400 font-normal">⚠ {warnCount}</span>
        )}

        {/* 红点闪烁徽标 */}
        {showBadge && (
          <span className="absolute top-1.5 right-3 flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500" />
          </span>
        )}
      </button>

      {/* 展开的日志时间线 */}
      {!collapsed && (
        <div className="px-4 pb-3 space-y-1 max-h-64 overflow-y-auto">
          {logs.map((log, i) => {
            const { icon, color, bg } = logStyle(log.level)
            const workerLabel = nodeLabels[log.node] || log.node
            return (
              <div
                key={`${log.step_id}-${i}`}
                className={`flex items-start gap-2.5 text-xs py-1.5 px-2 rounded ${bg}`}
              >
                {icon}
                <div className="min-w-0 flex-1">
                  {/* 头部：Worker名 + step_id */}
                  <div className="flex items-center gap-1.5 mb-0.5">
                    {workerLabel && (
                      <span className={color}>{workerLabel}</span>
                    )}
                    <span className="text-[#6e6e6e]">{log.step_id}</span>
                  </div>
                  {/* 消息摘要 */}
                  <span className={log.level === 'error' ? 'text-red-400' : 'text-[#ececec]'}>
                    {log.message}
                  </span>
                  {/* Payload 展示 — 折叠子区域 */}
                  {log.payload && Object.keys(log.payload).length > 0 && (
                    <PayloadView payload={log.payload} />
                  )}
                </div>
              </div>
            )
          })}

          {/* 流式进行中指示 */}
          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-[#8e8e8e] py-1.5 px-2">
              <Circle size={12} className="text-blue-400 animate-pulse shrink-0" />
              <span>等待更多日志...</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Payload 折叠展示 */
function PayloadView({ payload }: { payload: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)

  // 扁平化 payload 为可读行
  const entries = useMemo(() => {
    return Object.entries(payload).map(([k, v]) => {
      let display: string
      if (typeof v === 'string') {
        display = v.length > 120 ? v.slice(0, 120) + '...' : v
      } else if (typeof v === 'number' || typeof v === 'boolean') {
        display = String(v)
      } else if (Array.isArray(v)) {
        display = `[${v.length} 项] ${JSON.stringify(v).slice(0, 80)}`
      } else if (typeof v === 'object' && v !== null) {
        display = JSON.stringify(v, null, 0).slice(0, 120)
      } else {
        display = String(v)
      }
      return { key: k, display }
    })
  }, [payload])

  return (
    <div className="mt-1">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[10px] text-[#6e6e6e] hover:text-[#8e8e8e] transition-colors"
      >
        {open ? '▾ 收起详情' : '▸ 展开详情'} ({entries.length} 字段)
      </button>
      {open && (
        <div className="mt-1 space-y-0.5 max-h-32 overflow-y-auto bg-[#111] rounded p-1.5 font-mono text-[10px]">
          {entries.map(({ key, display }) => (
            <div key={key} className="flex gap-1.5">
              <span className="text-blue-400 shrink-0">{key}:</span>
              <span className="text-[#8e8e8e] break-all">{display}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
