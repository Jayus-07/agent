'use client'

import { useState } from 'react'
import { CheckCircle2, Clock, AlertCircle, ChevronDown, ChevronRight, Zap } from 'lucide-react'
import { MOCK_TRACE, MOCK_TOOL_CALLS, type TraceNode, type ToolCall } from '@/services/mock/trace'

const NODE_ICONS: Record<string, string> = {
  Planner: '📋', 'SQL Agent': '📊', 'SQL Execute': '💾', 'RAG Retrieve': '📚',
  Rerank: '🔄', Reporter: '📄', Answer: '✅',
}

const TOOL_ICONS: Record<string, string> = { sql: '📊', rag: '📚', http: '🌐', python: '🐍', browser: '🌍', search: '🔍' }

export default function AgentTimeline({ collapsed: outerCollapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const [expandedNode, setExpandedNode] = useState<string | null>(null)
  const [showTools, setShowTools] = useState(false)

  if (outerCollapsed) {
    return (
      <div className="border-b border-border-subtle bg-surface-elevated px-4 py-2">
        <button onClick={onToggle} className="flex items-center gap-2 text-xs text-accent hover:underline">
          <Zap size={12} /> Agent 执行中 · {MOCK_TRACE.filter(n => n.status === 'done').length}/{MOCK_TRACE.length} 节点完成
        </button>
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
          <span className="text-text-muted font-normal">{MOCK_TRACE.filter(n => n.status === 'done').length}/{MOCK_TRACE.length} 节点</span>
        </button>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowTools(!showTools)}
            className={`text-[10px] px-2 py-0.5 rounded-full transition-colors ${showTools ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
            Tool Calls
          </button>
          <span className="text-[10px] text-text-muted">{MOCK_TRACE.reduce((s, n) => s + n.elapsed, 0).toFixed(1)}s</span>
        </div>
      </div>

      {/* Tool Calls panel */}
      {showTools && (
        <div className="border-b border-border-subtle px-4 py-2 space-y-1.5">
          {MOCK_TOOL_CALLS.map(t => (
            <div key={t.id} className="flex items-center gap-2 text-[11px]">
              <span>{TOOL_ICONS[t.toolType]}</span>
              <span className="text-text-primary font-medium">{t.toolName}</span>
              <span className="text-text-muted">{t.duration}s</span>
              <span className={`ml-auto ${t.status === 'success' ? 'text-green-500' : 'text-red-500'}`}>
                {t.status === 'success' ? '✓' : '✗'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Timeline nodes */}
      <div className="px-4 py-2 space-y-1">
        {MOCK_TRACE.map((node, i) => (
          <TimelineNode key={node.name} node={node} isLast={i === MOCK_TRACE.length - 1}
            expanded={expandedNode === node.name} onToggle={() => setExpandedNode(expandedNode === node.name ? null : node.name)} />
        ))}
      </div>
    </div>
  )
}

function TimelineNode({ node, isLast, expanded, onToggle }: { node: TraceNode; isLast: boolean; expanded: boolean; onToggle: () => void }) {
  const statusIcon = node.status === 'done' ? <CheckCircle2 size={13} className="text-green-500" />
    : node.status === 'running' ? <Clock size={13} className="text-amber-500 animate-pulse" />
    : node.status === 'error' ? <AlertCircle size={13} className="text-red-500" />
    : <div className="w-[13px] h-[13px] rounded-full border-2 border-border-default" />

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
          <span className="text-[11px] text-text-primary font-medium">{NODE_ICONS[node.name] || '•'} {node.name}</span>
          <span className="text-[10px] text-text-muted ml-auto">{node.elapsed}s</span>
        </button>

        {/* Expandable detail */}
        {expanded && (
          <div className="mt-1.5 ml-5 space-y-1.5 text-[10px]">
            {node.prompt && <DetailBlock label="Prompt" content={node.prompt} />}
            {node.input && <DetailBlock label="Input" content={node.input} />}
            {node.output && <DetailBlock label="Output" content={node.output} />}
            {node.startTime && (
              <div className="flex gap-4 text-text-muted">
                <span>开始: {node.startTime}</span>
                <span>结束: {node.endTime}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function DetailBlock({ label, content }: { label: string; content: string }) {
  return (
    <div>
      <span className="text-text-muted font-medium">{label}</span>
      <pre className="mt-0.5 bg-surface-base rounded-md p-2 text-text-secondary whitespace-pre-wrap break-all max-h-32 overflow-y-auto font-mono">{content}</pre>
    </div>
  )
}
