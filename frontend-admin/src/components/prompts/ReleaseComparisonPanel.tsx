'use client'

import { useEffect, useState } from 'react'
import { GitCompareArrows, AlertTriangle, CheckCircle2 } from 'lucide-react'
import {
  getReleaseComparison,
  type ReleaseComparison,
} from '@/api/promptComparison'

/**
 * C1-4/REG-08：审批操作前的 candidate vs production 对比表。
 * 三列口径：production 最近 run（current）/ baseline / delta（候选 vs 基线）。
 * 无基线时显式显示 baseline unavailable（REG-10：不伪造 delta=0）。
 */
export default function ReleaseComparisonPanel({
  promptKey,
  releaseId,
}: {
  promptKey: string
  releaseId: string
}) {
  const [comparison, setComparison] = useState<ReleaseComparison | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    getReleaseComparison(promptKey, releaseId)
      .then(data => { if (!cancelled) setComparison(data) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [promptKey, releaseId])

  if (loading) {
    return <div className="rounded-lg border border-border-subtle bg-surface-base p-3 text-[11px] text-text-muted">加载对比数据…</div>
  }
  if (error || !comparison) {
    return <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-[11px] text-red-600">对比数据加载失败：{error || '未知错误'}</div>
  }

  const candidate = comparison.candidate
  const productionLatest = comparison.production_runs[0] || null
  const baselineNote = comparison.baseline.available
    ? comparison.baseline.run_id || '—'
    : (comparison.baseline.note || 'baseline unavailable')
  const rowOf = (label: string, current: number | null | undefined, base: number | null | undefined, delta?: number | null) => (
    <tr key={label} className="border-b border-border-subtle/60 last:border-0">
      <td className="px-2 py-1 text-text-secondary">{label}</td>
      <td className="px-2 py-1 font-mono">{formatNum(current)}</td>
      <td className="px-2 py-1 font-mono">{formatNum(base)}</td>
      <td className={`px-2 py-1 font-mono ${typeof delta === 'number' && delta < 0 ? 'text-red-500' : typeof delta === 'number' ? 'text-green-600' : 'text-text-muted'}`}>
        {typeof delta === 'number' ? (delta > 0 ? `+${delta.toFixed(4)}` : delta.toFixed(4)) : '—'}
      </td>
    </tr>
  )

  return (
    <section className="rounded-lg border border-border-subtle bg-surface-base p-3" aria-label="候选与生产对比">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-text-primary">
        <GitCompareArrows size={13} className="text-accent" />
        Candidate v{comparison.candidate_version} vs Production
        {comparison.production_version ? ` v${comparison.production_version}` : '（当前无 production 版本）'}
      </div>

      {/* 门禁判定摘要（GATE-03/11/12） */}
      {candidate.gate && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          <GateChip label="tier 门" pass={candidate.gate.tier_pass} />
          <GateChip label="样本量门" pass={candidate.gate.sample_pass} />
          <GateChip label="回归门" pass={candidate.gate.regression_pass} />
          <GateChip label="RAGAS 门" pass={candidate.gate.ragas_pass} />
        </div>
      )}
      {candidate.gate?.blocked_rules?.length ? (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-[10px] text-red-700">
          {candidate.gate.blocked_rules.map((rule, i) => (
            <p key={i} className="flex items-start gap-1">
              <AlertTriangle size={10} className="mt-0.5 shrink-0" />
              <span>{rule.rule}: 期望 {rule.expected}，实际 {rule.actual}</span>
            </p>
          ))}
        </div>
      ) : null}

      {/* candidate vs production / baseline 对比表 */}
      <table className="w-full mt-2 text-[11px]">
        <thead>
          <tr className="border-b border-border-subtle text-text-muted">
            <th className="px-2 py-1 text-left font-normal">指标</th>
            <th className="px-2 py-1 text-left font-normal">Candidate（候选）</th>
            <th className="px-2 py-1 text-left font-normal">Production（当前）</th>
            <th className="px-2 py-1 text-left font-normal">vs 基线 delta</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(candidate.metrics).map(([k, v]) => (
            <tr key={k} className="border-b border-border-subtle/60 last:border-0">
              <td className="px-2 py-1 text-text-secondary">{k}</td>
              <td className="px-2 py-1 font-mono">{formatNum(v)}</td>
              <td className="px-2 py-1 font-mono">{formatNum(productionLatest?.metrics?.[k])}</td>
              <td className="px-2 py-1 font-mono text-text-muted">{comparison.deltas.available ? '见回归明细' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* RAGAS 独立区块（RAGAS-12：不与自研混用） */}
      {Object.keys(candidate.ragas).length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] text-violet-700 font-medium">RAGAS（候选）</p>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {Object.entries(candidate.ragas).map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-violet-50 border border-violet-200 text-violet-700 font-mono text-[10px]">
                {k.replace('ragas_', '')}: {formatNum(v)}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-2 flex items-center gap-1 text-[10px] text-text-muted">
        {comparison.baseline.available ? <CheckCircle2 size={10} className="text-green-500" /> : <AlertTriangle size={10} className="text-amber-500" />}
        基线: {baselineNote}
        {comparison.deltas.available === false && comparison.deltas.note ? ` · ${comparison.deltas.note}` : ''}
      </div>
      {!productionLatest && (
        <p className="mt-1 text-[10px] text-text-muted">production 版本暂无关联评测 run（或台账不可达），当前列显示 —。</p>
      )}
    </section>
  )
}

function GateChip({ label, pass }: { label: string; pass: boolean | null | undefined }) {
  if (pass === null || pass === undefined) {
    return <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 text-gray-500">{label}: 未判定</span>
  }
  return pass
    ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-green-50 text-green-700 inline-flex items-center gap-0.5"><CheckCircle2 size={9} />{label}通过</span>
    : <span className="px-1.5 py-0.5 rounded text-[10px] bg-red-50 text-red-700 inline-flex items-center gap-0.5"><AlertTriangle size={9} />{label}未过</span>
}

function formatNum(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return typeof value === 'number' ? value.toFixed(4) : String(value)
}
