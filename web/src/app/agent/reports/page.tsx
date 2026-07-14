'use client'

import { FileText, ArrowUpRight } from 'lucide-react'
import { AGENT_REPORTS } from '@/services/mock/observability'

const TYPE_LABELS: Record<string, string> = { inventory_health: '库存健康', supplier_quality: '供应商评估', daily_sales: '销售日报' }

export default function ReportsPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">历史报告</h1>
          <p className="text-xs text-text-muted mt-1">AI 自动生成的运营分析报告</p>
        </div>
        <div className="space-y-3">
          {AGENT_REPORTS.map(r => (
            <div key={r.id} className="bg-surface-base rounded-xl border border-border-subtle p-5 hover:shadow-card transition-shadow">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <FileText size={18} className="text-accent" />
                  <span className="text-sm font-medium text-text-primary">{r.title}</span>
                  <span className="text-[10px] bg-accent/5 text-accent px-2 py-0.5 rounded-full">{TYPE_LABELS[r.type] || r.type}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-text-muted">
                  <span>{r.createdAt}</span>
                  <button className="flex items-center gap-1 text-accent hover:underline">查看 <ArrowUpRight size={11} /></button>
                </div>
              </div>
              <p className="text-xs text-text-muted leading-relaxed">{r.summary}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
