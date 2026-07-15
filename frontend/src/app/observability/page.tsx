'use client'

import { CheckCircle2, ArrowRight } from 'lucide-react'
import { AGENT_TRACES } from '@/services/mock/observability'

const NODE_COLORS: Record<string, string> = { Planner: '#4E79A7', 'SQL Agent': '#F28E2B', 'RAG Agent': '#59A14F', Reporter: '#B07AA1' }

export default function ObservabilityPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">Agent Trace</h1>
          <p className="text-xs text-text-muted mt-1">Multi-Agent 工作流执行追踪，每个节点耗时和状态</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[{ label: '总 Trace', value: AGENT_TRACES.length }, { label: '成功率', value: '100%' }, { label: '平均耗时', value: (AGENT_TRACES.reduce((s, t) => s + t.totalElapsed, 0) / AGENT_TRACES.length).toFixed(1) + 's' }, { label: '节点数', value: AGENT_TRACES.reduce((s, t) => s + t.nodes.length, 0) }].map(k => (
            <div key={k.label} className="bg-surface-base rounded-xl border border-border-subtle p-4 text-center">
              <div className="text-2xl font-bold text-text-primary">{k.value}</div>
              <div className="text-[11px] text-text-muted mt-1">{k.label}</div>
            </div>
          ))}
        </div>

        <div className="space-y-4">
          {AGENT_TRACES.map(t => (
            <div key={t.id} className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={14} className="text-green-500" />
                  <span className="text-sm text-text-primary">{t.question}</span>
                </div>
                <span className="text-xs text-text-muted">{t.totalElapsed}s</span>
              </div>
              <div className="flex items-center gap-2">
                {t.nodes.map((n, i) => (
                  <div key={n.name} className="flex items-center gap-1.5">
                    <div className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium" style={{ backgroundColor: (NODE_COLORS[n.name] || '#888') + '18', color: NODE_COLORS[n.name] }}>
                      {n.name} <span className="opacity-60">{n.elapsed}s</span>
                    </div>
                    {i < t.nodes.length - 1 && <ArrowRight size={10} className="text-text-muted" />}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
