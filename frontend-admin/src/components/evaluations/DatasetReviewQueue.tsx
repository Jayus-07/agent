'use client'

import { AlertTriangle, CheckCircle2, EyeOff, ShieldCheck, XCircle } from 'lucide-react'
import type { DatasetCandidate } from '@/api/evaluation'

interface DatasetReviewQueueProps {
  candidate?: DatasetCandidate
  candidates?: DatasetCandidate[]
  loading?: boolean
  onApprove?: (candidate: DatasetCandidate) => void | Promise<void>
  onReject?: (candidate: DatasetCandidate) => void | Promise<void>
}

export default function DatasetReviewQueue({
  candidate,
  candidates = candidate ? [candidate] : [],
  loading = false,
  onApprove,
  onReject,
}: DatasetReviewQueueProps) {
  return (
    <section className="rounded-xl border border-border-subtle bg-surface-base" aria-label="评测集候选审核">
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-text-primary">候选审核队列</h2>
          <p className="mt-0.5 text-[11px] text-text-muted">Trace、用户反馈和合成样本先脱敏、再进入人工审核</p>
        </div>
        <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">{candidates.length} 条待处理</span>
      </div>
      {candidates.length === 0 ? (
        <div className="px-4 py-8 text-center text-xs text-text-muted">暂无待审核候选</div>
      ) : (
        <div className="divide-y divide-border-subtle">
          {candidates.map(item => {
            const canApprove = item.redacted && item.status === 'pending_review'
            return (
              <article key={item.candidate_id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-[10px] text-text-muted">{item.candidate_id}</code>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-secondary">{item.source_type}</span>
                      {item.redacted ? (
                        <span className="inline-flex items-center gap-1 text-[10px] text-emerald-700"><ShieldCheck size={11} /> 已脱敏</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-[10px] text-red-700"><EyeOff size={11} /> 待脱敏</span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-text-primary">{item.question}</p>
                    {!item.redacted && (
                      <p className="mt-1 flex items-center gap-1 text-[10px] text-red-600"><AlertTriangle size={11} /> 仍可能包含生产 Trace/反馈中的敏感信息</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    {canApprove && onApprove && (
                      <button
                        type="button"
                        onClick={() => void onApprove(item)}
                        disabled={loading}
                        className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-[11px] text-white hover:bg-accent-hover disabled:opacity-40"
                      >
                        <CheckCircle2 size={12} /> 通过并生成版本
                      </button>
                    )}
                    {item.status === 'pending_review' && onReject && (
                      <button
                        type="button"
                        onClick={() => void onReject(item)}
                        disabled={loading}
                        className="inline-flex items-center gap-1 rounded-md border border-border-subtle px-2.5 py-1.5 text-[11px] text-text-secondary hover:text-red-700 disabled:opacity-40"
                      >
                        <XCircle size={12} /> 驳回
                      </button>
                    )}
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
