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
 *   - 新版 status 帧带节点执行起止时间，节点耗时只取后端实测值
 *   - 旧版 status 帧仍可回看节点顺序，但不再用相邻事件或总耗时伪造单节点时长
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
  elapsedSec: number | null      // 旧版历史没有真实耗时则为 null
  logs: LogEvent[]               // 该节点期间产出的 log 事件
  hasError: boolean              // 是否有 level=error 的 log
}

const STREAM_PHASE_LABELS: Record<string, string> = {
  understanding: '需求理解',
  table_routing: '选择数据表',
  sql_generation: '生成 SQL',
  sql_validation: '安全校验',
  tool_start: 'Tool 调用',
  tool_result: 'Tool 返回',
}

/** 把后端结构化阶段翻译成用户可读标签，管理端与用户端保持同一口径。 */
export function streamPhaseLabel(phase: unknown): string {
  return typeof phase === 'string' ? (STREAM_PHASE_LABELS[phase] ?? '执行进度') : '执行进度'
}

const STREAM_PROGRESS_BY_PHASE: Record<string, string> = {
  routing: '正在识别问题并选择处理路径…',
  capability_selection: '正在选择合适的查询能力…',
  understanding: '正在理解查询需求…',
  table_routing: '正在匹配可访问的数据表…',
  sql_generation: '正在生成 SQL 查询…',
  sql_validation: '正在校验 SQL 安全性…',
  rag_search: '正在检索知识库并核验资料…',
  answer_generation: '正在整理最终答复…',
}

const STREAM_PROGRESS_BY_NODE: Record<string, string> = {
  router: '正在识别问题并选择处理路径…',
  tool_selector: '正在选择合适的查询能力…',
  skill_executor: '正在执行所选查询步骤…',
  workflow_executor: '正在执行工作流步骤…',
  planner: '正在规划多步骤任务…',
  critique: '正在检查执行计划…',
  supervisor: '正在调度执行步骤…',
  sql_skill: '正在查询业务数据…',
  query_understanding: '正在理解数据查询需求…',
  table_router: '正在匹配可访问的数据表…',
  sql_generator: '正在生成 SQL 查询…',
  sql_validator: '正在校验 SQL 安全性…',
  sql_executor: '正在执行安全的数据查询…',
  rag_skill: '正在检索知识库并核验资料…',
  reporter: '正在整理最终答复…',
  general_chat: '正在生成回复…',
}

function progressForLog(log: LogEvent): string | null {
  const phase = log.payload?.phase
  const tool = log.payload?.tool
  if (typeof phase !== 'string') return null

  if (phase === 'tool_start') {
    if (tool === 'rag.search' || (typeof tool === 'string' && tool.includes('rag'))) {
      return STREAM_PROGRESS_BY_PHASE.rag_search
    }
    if (tool === 'sql.query' || (typeof tool === 'string' && tool.includes('sql'))) {
      return '正在执行安全的数据查询…'
    }
    return '正在执行所选查询步骤…'
  }
  if (phase === 'tool_result') {
    if (tool === 'rag.search' || (typeof tool === 'string' && tool.includes('rag'))) {
      return '知识库检索完成，正在整理答案…'
    }
    if (tool === 'sql.query' || (typeof tool === 'string' && tool.includes('sql'))) {
      return '数据查询完成，正在整理结果…'
    }
  }
  return STREAM_PROGRESS_BY_PHASE[phase] ?? null
}

/** 只按已收到的 SSE 状态/阶段事件生成文案，不估算百分比或虚构阶段。 */
export function streamProgressLabel(
  events: SSEStreamEvent[],
  currentStatus = '',
  nodeLabels: Record<string, string> = {},
): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i]
    if (event.event === 'log') {
      const progress = progressForLog(event.data)
      if (progress) return progress
    } else if (event.event === 'status') {
      if (event.data.phase === 'completed') return '当前步骤已完成，正在继续处理…'
      if (event.data.phase === 'failed' || event.data.phase === 'cancelled') return '正在收尾本次请求…'
      const progress = STREAM_PROGRESS_BY_NODE[event.data.node]
      if (progress) return progress
      const label = nodeLabels[event.data.node]
      if (label) return `正在执行「${label}」…`
    }
  }

  return STREAM_PROGRESS_BY_NODE[currentStatus]
    || (nodeLabels[currentStatus] ? `正在执行「${nodeLabels[currentStatus]}」…` : '')
    || '正在识别问题并选择处理路径…'
}

function ProgressLine({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-1.5 py-0.5 text-[11px] text-text-secondary">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
      {label}
    </div>
  )
}

function TimelineLogLine({ log }: { log: LogEvent }) {
  const phase = log.payload?.phase
  const tool = log.payload?.tool
  return (
    <div className="flex items-start gap-1.5 ml-2 py-0.5">
      <span className={`shrink-0 ${log.level === 'error' ? 'text-red-500' : log.level === 'warn' ? 'text-amber-500' : 'text-text-muted'}`}>
        {log.level === 'error' ? '✕' : log.level === 'warn' ? '⚠' : '•'}
      </span>
      {typeof phase === 'string' && (
        <span className="shrink-0 rounded bg-accent/10 px-1 text-[10px] text-accent">
          {streamPhaseLabel(phase)}
        </span>
      )}
      <span className="text-text-secondary flex-1">{log.message}</span>
      {typeof tool === 'string' && (
        <span className="shrink-0 font-mono text-[10px] text-text-muted">{tool}</span>
      )}
    </div>
  )
}

function PhaseStrip({ events }: { events: SSEStreamEvent[] }) {
  const phases = events
    .filter((event): event is Extract<SSEStreamEvent, { event: 'log' }> => event.event === 'log')
    .map(event => ({
      phase: event.data.payload?.phase,
      tool: event.data.payload?.tool,
    }))
    .filter(item => typeof item.phase === 'string')
    .slice(-6)
  if (phases.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-4 py-2 border-b border-border-subtle bg-surface-base/50">
      {phases.map((item, index) => (
        <span key={`${item.phase}-${index}`} className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] text-accent">
          {streamPhaseLabel(item.phase)}
          {typeof item.tool === 'string' && <span className="font-mono text-accent/70">· {item.tool}</span>}
        </span>
      ))}
    </div>
  )
}

/** 按 execution_id 合并起止帧；旧版事件没有实测时长时只显示节点状态。 */
function buildTimeline(events: SSEStreamEvent[], nodeLabels: Record<string, string>, isLoading: boolean): TimelineNode[] {
  const runs = new Map<string, {
    name: string
    startTs: number
    endTs: number | null
    durationMs: number | null
    status: string
    timed: boolean
  }>()
  for (const evt of events) {
    if (evt.event === 'status') {
      const status = evt.data
      const key = status.execution_id || `legacy:${status.node}`
      const existing = runs.get(key)
      const startTs = status.started_at ?? existing?.startTs ?? status.ts
      const endTs = status.finished_at ?? existing?.endTs ?? null
      const durationMs = typeof status.duration_ms === 'number'
        ? status.duration_ms
        : existing?.durationMs ?? null
      const eventStatus = status.status
        || (status.phase === 'failed' ? 'error' : status.phase === 'cancelled' ? 'cancelled' : '')
      runs.set(key, {
        name: status.node,
        startTs,
        endTs,
        durationMs,
        status: eventStatus || existing?.status || (status.phase === 'started' ? 'running' : ''),
        timed: Boolean(status.execution_id || status.duration_ms !== undefined),
      })
    }
  }
  const nodeOrder = [...runs.values()]
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

  const nowSec = Date.now() / 1000
  return nodeOrder.map((n, i) => {
    const isLegacyCurrent = !n.timed && i === nodeOrder.length - 1 && isLoading
    const isRunning = n.status === 'running' || isLegacyCurrent
    const elapsedSec = n.durationMs !== null
      ? Math.max(0, n.durationMs / 1000)
      : isRunning
        ? Math.max(0, nowSec - n.startTs)
        : null
    const logs = logsByNode[n.name] || []
    const hasError = logs.some((l) => l.level === 'error')
    const status = hasError || n.status === 'error' || n.status === 'cancelled'
      ? 'error'
      : isRunning
        ? 'running'
        : 'done'

    return {
      name: n.name,
      label: nodeLabels[n.name] || n.name,
      status,
      startTs: n.startTs,
      endTs: n.endTs,
      elapsedSec,
      logs,
      hasError,
    }
  })
}

function timelineElapsed(events: SSEStreamEvent[], isLoading: boolean, totalElapsedHint?: number): number | null {
  if (typeof totalElapsedHint === 'number' && totalElapsedHint > 0) return totalElapsedHint
  const timedStatuses = events
    .filter((event): event is Extract<SSEStreamEvent, { event: 'status' }> => event.event === 'status')
    .filter(event => typeof event.data.started_at === 'number')
  if (timedStatuses.length === 0) return null
  const starts = timedStatuses.map(event => event.data.started_at as number)
  const ends = timedStatuses
    .map(event => event.data.finished_at ?? (isLoading ? Date.now() / 1000 : null))
    .filter((value): value is number => value !== null)
  if (ends.length === 0) return null
  return Math.max(0, Math.max(...ends) - Math.min(...starts))
}

interface TimelineProps {
  collapsed: boolean
  onToggle: () => void
  /** 外部数据源（完成态回看：传入 done 时固化的快照）；缺省订阅 store（生成中） */
  events?: SSEStreamEvent[]
  nodeLabels?: Record<string, string>
  /** 完成态回看的总耗时（秒，来自 trace.elapsed）：末节点耗时据此定格 */
  totalElapsedHint?: number
  /** 无卡片形态：生成中与完成态回看均用，去掉边框/标题栏，纯文字缩进列表 */
  bare?: boolean
}

export default function AgentTimeline({ collapsed: outerCollapsed, onToggle, events: eventsProp, nodeLabels: labelsProp, totalElapsedHint, bare = false }: TimelineProps) {
  const [expandedNode, setExpandedNode] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)

  // 默认从 store 派生（生成中）；传入固化快照时以 props 为准（完成后回看，isLoading 恒 false）
  const storeEvents = useChatStore((s) => s.streamEvents)
  const storeLabels = useChatStore((s) => s.nodeLabels)
  const isLoading = useChatStore((s) => s.isLoading)
  const currentStatus = useChatStore((s) => s.currentStatus)
  const events = eventsProp ?? storeEvents
  const nodeLabels = labelsProp ?? storeLabels
  const progressLabel = streamProgressLabel(events, currentStatus, nodeLabels)

  const nodes = useMemo(
    () => buildTimeline(events, nodeLabels, isLoading),
    [events, nodeLabels, isLoading, totalElapsedHint],
  )
  const doneCount = nodes.filter((n) => n.status === 'done' || n.status === 'error').length
  const totalElapsed = timelineElapsed(events, isLoading, totalElapsedHint)
  const totalLogs = nodes.reduce((s, n) => s + n.logs.length, 0)

  // 折叠态：紧凑按钮（bare 形态不存在折叠态，折叠由外层行负责）
  if (outerCollapsed && !bare) {
    return (
      <div className="border border-border-subtle rounded-xl bg-surface-elevated px-4 py-2 overflow-hidden">
        <button onClick={onToggle} className="flex items-center gap-2 text-xs text-accent hover:underline">
          <Zap size={12} />
          {isLoading ? 'Agent 执行中' : (nodes.length > 0 ? 'Agent 执行完成' : 'Agent 待执行')}
          <span className="text-text-muted">· {doneCount}/{nodes.length} 节点</span>
        </button>
      </div>
    )
  }

  // 空态：bare 形态下只渲染一行等待文字，不出卡片
  if (nodes.length === 0) {
    if (bare) {
      if (!isLoading) return null
      return (
        <ProgressLine label={progressLabel} />
      )
    }
    const waiting = isLoading
    return (
      <div className="border border-border-subtle rounded-xl bg-surface-elevated overflow-hidden">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle">
          <div className="flex items-center gap-2 text-xs font-medium text-text-primary">
            <Zap size={13} className="text-accent" />
            Agent 执行时间线
          </div>
        </div>
        <div className={`px-4 py-6 text-center text-[11px] ${waiting ? 'text-text-secondary' : 'text-text-muted'}`}>
          {waiting ? (
            <ProgressLine label={progressLabel} />
          ) : (
            '发送问题后，将在此展示 LangGraph 多 Agent 执行过程'
          )}
        </div>
      </div>
    )
  }

  // bare 形态：无卡片，缩进列表 + 行内小字元信息，接在外层行下方
  if (bare) {
    return (
      <div className="pl-5 py-0.5">
        {isLoading && <ProgressLine label={progressLabel} />}
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] text-text-muted">{doneCount}/{nodes.length} 节点 · {totalElapsed === null ? '—' : `${totalElapsed.toFixed(1)}s`}</span>
          <button onClick={() => setShowLogs(!showLogs)}
            className={`text-[10px] transition-colors ${showLogs ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
            Logs {totalLogs > 0 && `(${totalLogs})`}
          </button>
        </div>
        <PhaseStrip events={events} />
        {showLogs && (
          <div className="pl-1 pb-2 space-y-2 max-h-48 overflow-y-auto">
            {nodes.map((n) => n.logs.length === 0 ? null : (
              <div key={`logs-${n.name}`} className="text-[11px]">
                <div className="text-text-muted font-medium mb-0.5">{n.label}</div>
                {n.logs.map((l, i) => <TimelineLogLine key={i} log={l} />)}
              </div>
            ))}
          </div>
        )}
        <div className="space-y-1">
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

  return (
    <div className="border border-border-subtle rounded-xl bg-surface-elevated overflow-hidden">
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
          <span className="text-[10px] text-text-muted">{totalElapsed === null ? '—' : `${totalElapsed.toFixed(1)}s`}</span>
        </div>
      </div>

      {isLoading && <div className="px-4 pt-2"><ProgressLine label={progressLabel} /></div>}
      <PhaseStrip events={events} />

      {/* Logs panel（按节点分组的所有 log 事件） */}
      {showLogs && (
        <div className="border-b border-border-subtle px-4 py-2 space-y-2 max-h-48 overflow-y-auto">
          {nodes.map((n) => n.logs.length === 0 ? null : (
            <div key={`logs-${n.name}`} className="text-[11px]">
              <div className="text-text-muted font-medium mb-0.5">{n.label}</div>
              {n.logs.map((l, i) => <TimelineLogLine key={i} log={l} />)}
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
            isCurrent={currentStatus === node.name && node.status === 'running'}
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
          <span className="text-[10px] text-text-muted ml-auto tabular-nums">{node.elapsedSec === null ? '—' : `${node.elapsedSec.toFixed(2)}s`}</span>
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
