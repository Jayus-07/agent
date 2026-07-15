'use client'

import { useState } from 'react'
import { Database, ChevronDown, ChevronRight, BarChart3, Table2 } from 'lucide-react'
import { MOCK_SQL_VIZ } from '@/services/mock/trace'

export default function SqlViz() {
  const [showExplain, setShowExplain] = useState(false)
  const [view, setView] = useState<'table' | 'chart'>('table')

  const d = MOCK_SQL_VIZ
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
          <button onClick={() => setView('chart')}
            className={`px-2 py-1 rounded text-[10px] transition-colors ${view === 'chart' ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-text-secondary'}`}>
            <BarChart3 size={11} className="inline mr-1" />图表
          </button>
        </div>
      </div>

      {/* SQL Query */}
      <div className="px-3 py-2 border-b border-border-subtle bg-surface-base/50">
        <pre className="text-[10px] text-text-secondary font-mono whitespace-pre-wrap">{d.query}</pre>
      </div>

      {/* EXPLAIN (collapsible) */}
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

      {/* Table View */}
      {view === 'table' && (
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
                  {Object.values(row).map((v, j) => <td key={j} className="px-3 py-1.5 text-text-primary">{v}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Chart View */}
      {view === 'chart' && (
        <div className="p-3 space-y-2">
          {d.chartData.labels.map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <span className="text-[10px] text-text-secondary w-20 truncate text-right">{label}</span>
              <div className="flex-1 h-5 rounded-sm bg-surface-base overflow-hidden">
                <div className="h-full rounded-sm bg-accent flex items-center justify-end pr-1.5 transition-all duration-500"
                  style={{ width: `${(d.chartData.values[i] / maxVal) * 100}%` }}>
                  <span className="text-[9px] text-white font-medium">¥{d.chartData.values[i]}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* AI Summary */}
      <div className="px-3 py-2 bg-accent/3 border-t border-border-subtle text-[10px] text-text-secondary leading-relaxed">
        🤖 {d.summary}
      </div>
    </div>
  )
}
