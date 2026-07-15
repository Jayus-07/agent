'use client'

import { RAG_METRICS } from '@/services/mock/observability'

export default function RagMetricsPage() {
  const m = RAG_METRICS

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">RAG 指标</h1>
          <p className="text-xs text-text-muted mt-1">检索系统核心指标：检索次数、平均耗时、score 分布、rerank 统计</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
          {[{ label: '检索次数', value: m.totalSearches.toLocaleString() }, { label: '平均耗时', value: m.avgElapsed.toFixed(2) + 's' }, { label: '平均 Score', value: m.avgScore.toFixed(2) }, { label: 'Rerank 数', value: m.rerankCount.toLocaleString() }, { label: 'Token 消耗', value: (m.tokenUsage / 1000000).toFixed(1) + 'M' }].map(k => (
            <div key={k.label} className="bg-surface-base rounded-xl border border-border-subtle p-4 text-center">
              <div className="text-xl font-bold text-text-primary">{k.value}</div>
              <div className="text-[11px] text-text-muted mt-1">{k.label}</div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
            <h3 className="text-sm font-semibold text-text-primary mb-3">热门查询</h3>
            <div className="space-y-2">
              {m.topQueries.map((q, i) => (
                <div key={q.query} className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-text-muted w-4">#{i + 1}</span>
                    <span className="text-xs text-text-primary">{q.query}</span>
                  </div>
                  <span className="text-xs text-text-muted">{q.count} 次</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
            <h3 className="text-sm font-semibold text-text-primary mb-3">检索漏斗</h3>
            <div className="space-y-3">
              {[{ label: '总检索', count: m.totalSearches, pct: 100 }, { label: 'Rerank', count: m.rerankCount, pct: Math.round(m.rerankCount / m.totalSearches * 100) }, { label: 'Top-K 返回', count: 25160, pct: 100 }].map(s => (
                <div key={s.label}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-text-secondary">{s.label}</span>
                    <span className="text-xs text-text-muted">{s.count.toLocaleString()} ({s.pct}%)</span>
                  </div>
                  <div className="h-2 rounded-full bg-surface-elevated overflow-hidden">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${s.pct}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
