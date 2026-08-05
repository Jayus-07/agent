'use client'

import { useState, useMemo } from 'react'
import { Database, ChevronDown, ChevronRight, BarChart3, Table2 } from 'lucide-react'
import type { SSEStreamEvent } from '@/lib/types'

interface Props {
  /** SSE v2 流式事件列表，从中提取 sql_worker 的输出 */
  streamEvents?: SSEStreamEvent[]
}

interface SqlData {
  query: string
  explain?: string
  summary?: string
  data: Record<string, string | number>[]
  chartData: { labels: string[]; values: number[] }
}

/** 从 streamEvents 中提取 sql_worker 的 SQL 数据 */
function extractSqlData(events?: SSEStreamEvent[]): SqlData | null {
  if (!events?.length) return null

  const sqlEvents = events.filter(
    e => e.event === 'log' && e.data.node === 'sql_worker'
  )
  if (!sqlEvents.length) return null

  // 找最后一个 sql_worker 事件（含完整结果）
  const lastSql = sqlEvents[sqlEvents.length - 1].data as import('@/lib/types').LogEvent
  const p = lastSql.payload || {}

  const query = (p.sql as string) || (p.query as string) || ''
  if (!query) return null

  const rows = (p.rows as Record<string, string | number>[]) || (p.data as Record<string, string | number>[]) || []
  const cols = (p.columns as string[]) || (Object.keys(rows[0] || {}))

  // 图表数据：取前 10 行，数字列用第一列做 label
  const chartLabels = rows.slice(0, 10).map(r => String(Object.values(r)[0] || ''))
  const chartValues = rows.slice(0, 10).map(r => {
    const nums = Object.values(r).filter(v => typeof v === 'number')
    return nums.length > 1 ? Number(nums[1]) : Number(nums[0]) || 0
  })

  return {
    query,
    explain: (p.explain as string) || undefined,
    summary: (p.summary as string) || (p.ai_summary as string) || undefined,
    data: rows.slice(0, 20),
    chartData: { labels: chartLabels, values: chartValues },
  }
}

export default function SqlViz({ streamEvents }: Props) {
  const [showExplain, setShowExplain] = useState(false)
  const [view, setView] = useState<'table' | 'chart'>('table')

  const d = useMemo(() => extractSqlData(streamEvents), [streamEvents])

  if (!d) return null

  const maxVal = Math.max(...d.chartData.values, 1)

  return (
    <div className="bg-surface-elevated rounded-xl border border-border-subtle overflow-hidden my-2 text-xs">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface-base border-b border-border-subtle">
        <div className="flex items-center gap-1.5 text-text-primary font-medium">
          <Database size={12} className="text-accent" /> SQL 查询结果
        </div>
        <div className="flex items-center gap-0.5">
          <button onClick={() => setView('table')}
            className={`px-2 py-1 rounded text-[10px] transition-colors ${view === 'table' ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
            <Table2 size={11} className="inline mr-1" />表格
          </button>
          {d.chartData.values.some(v => v > 0) && (
            <button onClick={() => setView('chart')}
              className={`px-2 py-1 rounded text-[10px] transition-colors ${view === 'chart' ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
              <BarChart3 size={11} className="inline mr-1" />图表
            </button>
          )}
        </div>
      </div>

      {/* SQL Query */}
      <div className="px-3 py-2 border-b border-border-subtle bg-surface-base/50">
        <pre className="text-[10px] text-text-secondary font-mono whitespace-pre-wrap">{d.query}</pre>
      </div>

      {/* EXPLAIN (collapsible) */}
      {d.explain && (
        <div className="border-b border-border-subtle">
          <button onClick={() => setShowExplain(!showExplain)}
            className="w-full flex items-center gap-1 px-3 py-1.5 text-[10px] text-text-muted hover:text-text-secondary transition-colors">
            {showExplain ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            EXPLAIN
          </button>
          {showExplain && (
            <pre className="px-3 pb-2 text-[10px] text-text-muted font-mono whitespace-pre-wrap">{d.explain}</pre>
          )}
        </div>
      )}

      {/* Table View */}
      {view === 'table' && d.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-border-subtle bg-surface-base/50 text-text-muted">
                {Object.keys(d.data[0]).map(k => <th key={k} className="text-left px-3 py-1.5 font-medium">{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {d.data.map((row, i) => (
                <tr key={i} className="border-b border-border-subtle last:border-0 hover:bg-surface-hover transition-colors">
                  {Object.values(row).map((v, j) => <td key={j} className="px-3 py-1.5 text-text-primary">{String(v)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Chart View */}
      {view === 'chart' && d.chartData.values.some(v => v > 0) && (
        <div className="p-3 space-y-2">
          {d.chartData.labels.map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <span className="text-[10px] text-text-secondary w-20 truncate text-right">{label}</span>
              <div className="flex-1 h-5 rounded-sm bg-surface-base overflow-hidden">
                <div className="h-full rounded-sm bg-accent flex items-center justify-end pr-1.5 transition-all duration-500"
                  style={{ width: `${(d.chartData.values[i] / maxVal) * 100}%` }}>
                  <span className="text-[9px] text-white font-medium">{d.chartData.values[i]}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* AI Summary */}
      {d.summary && (
        <div className="px-3 py-2 bg-accent/3 border-t border-border-subtle text-[10px] text-text-secondary leading-relaxed">
          🤖 {d.summary}
        </div>
      )}
    </div>
  )
}
