'use client'

/**
 * AgentTimeline — 实时展示 LangGraph 多 Agent 执行过程
 *
 * 数据源：useChatStore.streamEvents（SSE v2 事件流）
 *   - status 事件 → 节点切换（planner / supervisor / workers / reporter）
 *   - log 事件 → 节点内的子步骤详情（payload 含入参/出参）
 *   - meta 事件 → nodeLabels 映射表（emoji + 中文标签）
 *
 * 设计原则：
 *   - 不持有独立 state，全部从 store 派生（避免双重数据源）
 *   - 后端只发节点"进入"事件，无"退出"事件 → 根据 currentStatus + isLoading 推断节点状态
 *   - isLoading=false 且最后一个 status 事件即当前 → 该节点标记为 done（见 store.replaceLastAssistant 的 done 事件之后）
 */

import { useMemo, useState } from 'react'
import { CheckCircle2, Clock, AlertCircle, ChevronDown, ChevronRight, Zap, Circle } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import type { SSEStreamEvent, LogEvent } from '@/lib/types'

interface TimelineNode {
  name: string
  label: string                  // emoji + 中文，来自 nodeLabels
  status: 'pending' | 'running' | 'done' | 'error'
  startTs: number                // 节点首次出现的 status.ts
  endTs: number | null           // 下一个节点开始时 / 流结束时
  elapsedSec: number             // endTs - startTs
  logs: LogEvent[]               // 该节点期间产出的 log 事件
  hasError: boolean              // 是否有 level=error 的 log
}

/** 从 SSE 事件流派生 TimelineNode[] */
function buildTimeline(events: SSEStreamEvent[], nodeLabels: Record<string, string>, isLoading: boolean): TimelineNode[] {
  // 按出现顺序收集所有 status 节点
  const nodeOrder: { name: string; ts: number }[] = []
  for (const evt of events) {
    if (evt.event === 'status') {
      // 同名节点多次出现时（如 supervisor 多轮调度），保留最后一次
      const existing = nodeOrder.find((n) => n.name === evt.data.node)
      if (!existing) nodeOrder.push({ name: evt.data.node, ts: evt.data.ts })
    }
  }
  if (nodeOrder.length === 0) return []

  // 关联 log 事件到所属节点
  const logsByNode: Record<string, LogEvent[]> = {}
  for (const evt of events) {
    if (evt.event === 'log') {
      const node = evt.data.node
      if (!logsByNode[node]) logsByNode[node] = []
      logsByNode[node].push(evt.data)
    }
  }

  // 计算每个节点的结束时间 = 下一个节点的开始时间
  const currentNodeName = nodeOrder[nodeOrder.length - 1].name
  const nowSec = Date.now() / 1000
  return nodeOrder.map((n, i) => {
    const nextTs = i < nodeOrder.length - 1 ? nodeOrder[i + 1].ts : null
    const isLast = i === nodeOrder.length - 1
    const isRunning = isLast && isLoading
    const endTs = isRunning ? null : nextTs
    const elapsedSec = endTs !== null ? Math.max(0, endTs - n.ts) : Math.max(0, nowSec - n.ts)
    const logs = logsByNode[n.name] || []
    const hasError = logs.some((l) => l.level === 'error')

    return {
      name: n.name,
      label: nodeLabels[n.name] || n.name,
      status: hasError ? 'error' : isRunning ? 'running' : 'done',
      startTs: n.ts,
      endTs,
      elapsedSec,
      logs,
      hasError,
    }
  })
}

export default function AgentTimeline({ collapsed: outerCollapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const [expandedNode, setExpandedNode] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)

  // 全部从 store 派生，无本地 state
  const events = useChatStore((s) => s.streamEvents)
  const nodeLabels = useChatStore((s) => s.nodeLabels)
  const isLoading = useChatStore((s) => s.isLoading)
  const currentStatus = useChatStore((s) => s.currentStatus)

  const nodes = useMemo(() => buildTimeline(events, nodeLabels, isLoading), [events, nodeLabels, isLoading])
  const doneCount = nodes.filter((n) => n.status === 'done' || n.status === 'error').length
  const totalElapsed = nodes.reduce((s, n) => s + n.elapsedSec, 0)
  const totalLogs = nodes.reduce((s, n) => s + n.logs.length, 0)

  // 折叠态：紧凑按钮
  if (outerCollapsed) {
    return (
      <div className="border-b border-border-subtle bg-surface-elevated px-4 py-2">
        <button onClick={onToggle} className="flex items-center gap-2 text-xs text-accent hover:underline">
          <Zap size={12} />
          {isLoading ? 'Agent 执行中' : (nodes.length > 0 ? 'Agent 执行完成' : 'Agent 待执行')}
          <span className="text-text-muted">· {doneCount}/{nodes.length} 节点</span>
        </button>
      </div>
    )
  }

  // 空态：未开始
  if (nodes.length === 0) {
    return (
      <div className="border-b border-border-subtle bg-surface-elevated">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
          <div className="flex items-center gap-2 text-xs font-medium text-text-primary">
            <Zap size={13} className="text-accent" />
            Agent 执行时间线
          </div>
        </div>
        <div className="px-4 py-6 text-center text-[11px] text-text-muted">
          发送问题后，将在此展示 LangGraph 多 Agent 执行过程
        </div>
      </div>
    )
  }

  return (
    <div className="border-b border-border-subtle bg-surface-elevated">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
        <button onClick={onToggle} className="flex items-center gap-2 text-xs font-medium text-text-primary">
          <Zap size={13} className="text-accent" />
          Agent 执行时间线
          <span className="text-text-muted font-normal">{doneCount}/{nodes.length} 节点</span>
        </button>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowLogs(!showLogs)}
            className={`text-[10px] px-2 py-0.5 rounded-full transition-colors ${showLogs ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
            Logs {totalLogs > 0 && `(${totalLogs})`}
          </button>
          <span className="text-[10px] text-text-muted">{totalElapsed.toFixed(1)}s</span>
        </div>
      </div>

      {/* Logs panel（按节点分组的所有 log 事件） */}
      {showLogs && (
        <div className="border-b border-border-subtle px-4 py-2 space-y-2 max-h-48 overflow-y-auto">
          {nodes.map((n) => n.logs.length === 0 ? null : (
            <div key={`logs-${n.name}`} className="text-[11px]">
              <div className="text-text-muted font-medium mb-0.5">{n.label}</div>
              {n.logs.map((l, i) => (
                <div key={i} className="flex items-start gap-1.5 ml-2 py-0.5">
                  <span className={`shrink-0 ${l.level === 'error' ? 'text-red-500' : l.level === 'warn' ? 'text-amber-500' : 'text-text-muted'}`}>
                    {l.level === 'error' ? '✕' : l.level === 'warn' ? '⚠' : '•'}
                  </span>
                  <span className="text-text-secondary flex-1">{l.message}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* Timeline nodes */}
      <div className="px-4 py-2 space-y-1">
        {nodes.map((node, i) => (
          <TimelineNodeRow key={`${node.name}-${i}`} node={node} isLast={i === nodes.length - 1}
            expanded={expandedNode === node.name}
            onToggle={() => setExpandedNode(expandedNode === node.name ? null : node.name)}
            isCurrent={currentStatus === node.name}
          />
        ))}
      </div>
    </div>
  )
}

function TimelineNodeRow({ node, isLast, expanded, onToggle, isCurrent }: {
  node: TimelineNode
  isLast: boolean
  expanded: boolean
  onToggle: () => void
  isCurrent: boolean
}) {
  const statusIcon =
    node.status === 'done' ? <CheckCircle2 size={13} className="text-green-500" />
    : node.status === 'running' ? <Clock size={13} className="text-amber-500 animate-pulse" />
    : node.status === 'error' ? <AlertCircle size={13} className="text-red-500" />
    : <Circle size={13} className="text-text-muted" />

  return (
    <div className="flex gap-2">
      {/* Timeline line */}
      <div className="flex flex-col items-center pt-0.5">
        {statusIcon}
        {!isLast && <div className="w-px flex-1 bg-border-subtle mt-0.5" />}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pb-2">
        <button onClick={onToggle} className="w-full flex items-center gap-1.5 text-left group">
          {expanded ? <ChevronDown size={11} className="text-text-muted" /> : <ChevronRight size={11} className="text-text-muted" />}
          <span className="text-[11px] text-text-primary font-medium">{node.label}</span>
          {isCurrent && <span className="text-[9px] text-amber-500 bg-amber-50 px-1 rounded">running</span>}
          <span className="text-[10px] text-text-muted ml-auto tabular-nums">{node.elapsedSec.toFixed(2)}s</span>
        </button>

        {/* Expandable detail：展示该节点的 logs */}
        {expanded && (
          <div className="mt-1.5 ml-5 space-y-1.5 text-[10px]">
            {node.logs.length === 0 ? (
              <div className="text-text-muted italic">无详细日志</div>
            ) : (
              node.logs.map((l, i) => (
                <div key={i} className="border-l-2 border-border-subtle pl-2 py-0.5">
                  <div className="flex items-center gap-1.5">
                    <span className={`text-[9px] uppercase ${l.level === 'error' ? 'text-red-500' : l.level === 'warn' ? 'text-amber-500' : 'text-text-muted'}`}>
                      {l.level}
                    </span>
                    <span className="text-text-muted">{l.step_id}</span>
                  </div>
                  <div className="text-text-secondary mt-0.5">{l.message}</div>
                  {l.payload && Object.keys(l.payload).length > 0 && (
                    <pre className="mt-0.5 bg-surface-base rounded-md p-1.5 text-text-muted whitespace-pre-wrap break-all max-h-24 overflow-y-auto font-mono text-[9px]">
                      {JSON.stringify(l.payload, null, 2)}
                    </pre>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}