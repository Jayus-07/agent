'use client'

import { Activity, GitCommitHorizontal, Layers3, ShieldCheck } from 'lucide-react'
import type { EvalRunDetail } from '@/api/evaluation'

export default function EvaluationRunDetail({ run }: { run: EvalRunDetail | null }) {
  if (!run) return <div className="rounded-xl border border-border-subtle bg-surface-base px-4 py-8 text-center text-xs text-text-muted">选择一次运行查看 provenance</div>
  const provenance = (run.meta.eval_provenance || {}) as Record<string, unknown>
  const snapshot = (provenance.prompt_snapshot || run.report.metadata?.prompt_snapshot || {}) as Record<string, unknown>
  return (
    <section className="rounded-xl border border-border-subtle bg-surface-base p-4" aria-label="评测运行详情">
      <div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-text-primary">运行 provenance</h2><p className="mt-1 font-mono text-[10px] text-text-muted">{run.run_id}</p></div><Activity size={15} className="text-accent" /></div>
      <div className="mt-4 grid grid-cols-2 gap-3 border-y border-border-subtle py-3 md:grid-cols-4"><Info label="Suite" value={String(provenance.suite || '—')} /><Info label="数据版本" value={String(provenance.dataset_version || '—')} /><Info label="KB / Fixture" value={`${String(provenance.kb_id || '—')} / ${String(provenance.fixture_set || '—')}`} /><Info label="Git SHA" value={String(provenance.git_sha || run.meta.git_sha || '—')} mono /></div>
      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-text-secondary"><span className="inline-flex items-center gap-1 rounded-md bg-slate-50 px-2 py-1"><Layers3 size={11} /> Prompt {Object.keys(snapshot).length ? `${Object.keys(snapshot).length} 个版本` : '—'}</span><span className="inline-flex items-center gap-1 rounded-md bg-slate-50 px-2 py-1"><ShieldCheck size={11} /> Tool {String(provenance.tool_contract_fingerprint || '—')}</span><span className="inline-flex items-center gap-1 rounded-md bg-slate-50 px-2 py-1"><GitCommitHorizontal size={11} /> Model {String(provenance.model_binding_fingerprint || '—')}</span></div>
    </section>
  )
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="min-w-0"><div className="text-[10px] text-text-muted">{label}</div><div className={`mt-1 truncate text-xs text-text-primary ${mono ? 'font-mono' : ''}`} title={value}>{value || '—'}</div></div> }
