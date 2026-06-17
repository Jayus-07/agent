'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, Circle, SkipForward } from 'lucide-react'
import type { SSEEvent } from '@/lib/types'

const WORKER_ICONS: Record<string, string> = {
  sql_worker: '📊',
  rag_worker: '📚',
  report_worker: '📄',
  planner: '🧠',
}

function statusIcon(status?: string) {
  switch (status) {
    case 'success': return <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
    case 'failed':  return <XCircle size={14} className="text-red-400 shrink-0" />
    case 'running': return <Circle size={14} className="text-blue-400 shrink-0" />
    case 'skipped': return <SkipForward size={14} className="text-[#8e8e8e] shrink-0" />
    default:        return null
  }
}

interface RenderItem {
  icon: React.ReactNode
  label: string
  detail: string
  extra?: React.ReactNode
  isError?: boolean
}

function stageToItem(e: SSEEvent): RenderItem | null {
  const icon = WORKER_ICONS[e.node] ?? ''

  switch (e.stage) {
    case 'planning':
      return {
        icon: <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />,
        label: e.label,
        detail: e.message,
        extra: e.data.tasks && e.data.tasks.length > 0 ? (
          <ul className="mt-1 space-y-0.5">
            {e.data.tasks.map((t: string, j: number) => (
              <li key={j} className="text-[#8e8e8e] pl-3">{j + 1}. {t}</li>
            ))}
          </ul>
        ) : undefined,
      }

    case 'executing':
      return {
        icon: statusIcon(e.data.status),
        label: `${icon} ${e.label}`,
        detail: e.data.description || e.message,
        extra: e.data.elapsed != null ? (
          <span className="text-[#6e6e6e] ml-1.5 tabular-nums">{e.data.elapsed.toFixed(1)}s</span>
        ) : undefined,
        isError: e.data.status === 'failed',
      }

    case 'done':
      return {
        icon: <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />,
        label: '',
        detail: e.message,
      }

    case 'error':
      return {
        icon: <XCircle size={14} className="text-red-400 shrink-0 mt-0.5" />,
        label: '',
        detail: e.message,
        isError: true,
      }

    default:
      return null
  }
}

export default function ThinkingPanel({ events }: { events: SSEEvent[] }) {
  const [collapsed, setCollapsed] = useState(false)

  if (events.length === 0) return null

  // Deduplicate: keep latest state per step_id or stage
  const seen = new Map<string, SSEEvent>()
  for (const e of events) {
    if (e.stage === 'executing' && e.data.step_id) {
      seen.set(e.data.step_id, e)
    } else if (e.stage === 'planning' || e.stage === 'done' || e.stage === 'error') {
      seen.set(e.stage, e)
    }
  }

  const items = Array.from(seen.values())
    .map(stageToItem)
    .filter((item): item is RenderItem => item !== null)
  const hasError = items.some((item) => item.isError)
  const allDone = events.some((e) => e.stage === 'done')
  const successCount = events.filter((e) => e.stage === 'executing' && e.data.status === 'success').length
  const totalSteps = items.length

  return (
    <div className="mb-4 border border-[#3f3f3f] rounded-xl overflow-hidden bg-[#1a1a1a]">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className={`w-full flex items-center gap-2 px-4 py-2.5 text-xs font-medium transition-colors ${
          hasError ? 'text-red-400' : allDone ? 'text-emerald-400' : 'text-[#b4b4b4]'
        }`}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span>思考过程</span>
        <span className="text-[#8e8e8e] font-normal">({successCount}/{totalSteps} 步)</span>
      </button>

      {!collapsed && (
        <div className="px-4 pb-3 space-y-1.5">
          {items.map((item, i) => (
            <div key={i} className="flex items-start gap-2.5 text-xs py-1">
              {item.icon}
              <div className="min-w-0">
                {item.label && (
                  <span className={item.isError ? 'text-red-400' : 'text-[#ececec]'}>
                    {item.label}
                  </span>
                )}
                <span className={item.isError ? 'text-red-400/80' : 'text-[#8e8e8e]'}>
                  {item.label ? ' ' : ''}{item.detail}
                </span>
                {item.extra}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
