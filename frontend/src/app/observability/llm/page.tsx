'use client'

import { CheckCircle2, AlertCircle } from 'lucide-react'
import { LLM_CALLS } from '@/services/mock/observability'

export default function LlmPage() {
  const totalPrompt = LLM_CALLS.reduce((s, c) => s + c.promptTokens, 0)
  const totalCompletion = LLM_CALLS.reduce((s, c) => s + c.completionTokens, 0)
  const avgElapsed = LLM_CALLS.reduce((s, c) => s + c.elapsed, 0) / LLM_CALLS.length

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">LLM 调用</h1>
          <p className="text-xs text-text-muted mt-1">每次 LLM 调用的 token 消耗、耗时、节点来源</p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[{ label: '总调用', value: LLM_CALLS.length }, { label: 'Prompt Tokens', value: totalPrompt.toLocaleString() }, { label: 'Completion Tokens', value: totalCompletion.toLocaleString() }, { label: '平均耗时', value: avgElapsed.toFixed(1) + 's' }].map(k => (
            <div key={k.label} className="bg-surface-base rounded-xl border border-border-subtle p-4 text-center">
              <div className="text-xl font-bold text-text-primary">{k.value}</div>
              <div className="text-[11px] text-text-muted mt-1">{k.label}</div>
            </div>
          ))}
        </div>

        <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
              {['时间', '模型', '节点', 'Prompt', 'Completion', '耗时', '状态'].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {LLM_CALLS.map(c => (
                <tr key={c.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-2.5 text-text-muted">{c.timestamp}</td>
                  <td className="px-4 py-2.5 text-text-primary font-mono">{c.model}</td>
                  <td className="px-4 py-2.5"><span className="px-2 py-0.5 rounded bg-surface-elevated text-text-secondary">{c.node}</span></td>
                  <td className="px-4 py-2.5">{c.promptTokens.toLocaleString()}</td>
                  <td className="px-4 py-2.5">{c.completionTokens.toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-text-muted">{c.elapsed}s</td>
                  <td className="px-4 py-2.5">{c.status === 'success' ? <CheckCircle2 size={13} className="text-green-500" /> : <AlertCircle size={13} className="text-red-500" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
