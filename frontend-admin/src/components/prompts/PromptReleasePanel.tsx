'use client'

import { AlertTriangle, CheckCircle2, ExternalLink, GitBranch, RotateCcw, Send, ShieldCheck } from 'lucide-react'
import type { PromptReleaseRecord, PromptRuntimeStatus } from '@/api/prompts'

export type PromptReleaseView = PromptReleaseRecord

interface PromptReleasePanelProps {
  release: PromptReleaseView
  onApprove?: () => void | Promise<void>
  onPublish: () => void | Promise<void>
  onRollback?: () => void | Promise<void>
  loading?: boolean
}

const STATUS_META: Record<string, { label: string; className: string }> = {
  pending: { label: '待评测', className: 'bg-slate-100 text-slate-600' },
  running: { label: '评测中', className: 'bg-amber-100 text-amber-700' },
  failed: { label: '未通过', className: 'bg-red-100 text-red-700' },
  passed: { label: '评测通过', className: 'bg-emerald-100 text-emerald-700' },
  approved: { label: '待发布', className: 'bg-blue-100 text-blue-700' },
  published: { label: '已发布', className: 'bg-teal-100 text-teal-700' },
  rolled_back: { label: '已回滚', className: 'bg-slate-100 text-slate-600' },
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    return value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : String(value)
  }
  return String(value)
}

function runtimeProblems(runtime?: PromptRuntimeStatus): string[] {
  return (runtime?.processes ?? [])
    .filter(process => ['stale', 'degraded', 'unknown'].includes(process.status))
    .map(process => process.name || process.instance_id)
}

export default function PromptReleasePanel({
  release,
  onApprove,
  onPublish,
  onRollback,
  loading = false,
}: PromptReleasePanelProps) {
  const status = STATUS_META[release.status] ?? STATUS_META.pending
  const problems = runtimeProblems(release.runtime_status)
  const canPublish = release.status === 'approved' && !loading
  const rollbackVisible = Boolean(onRollback) && (
    release.status === 'published' || problems.length > 0
  )
  const provenance = release.dataset_provenance || {}

  return (
    <section className="rounded-xl border border-border-subtle bg-surface-base p-4 shadow-sm" aria-label="Prompt 发布门禁">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <GitBranch size={15} className="text-accent" />
            <h2 className="text-sm font-semibold text-text-primary">发布门禁</h2>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${status.className}`}>
              {status.label}
            </span>
          </div>
          <p className="mt-1 font-mono text-[11px] text-text-muted">
            {release.release_id} · 候选 v{release.version} · {release.executor}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {release.status === 'passed' && onApprove && (
            <button
              type="button"
              onClick={() => void onApprove()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-50 disabled:opacity-50"
            >
              <ShieldCheck size={13} /> 审批通过
            </button>
          )}
          <button
            type="button"
            onClick={() => void onPublish()}
            disabled={!canPublish}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={13} /> 发布到生产
          </button>
          {rollbackVisible && (
            <button
              type="button"
              onClick={() => void onRollback?.()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary disabled:opacity-40"
            >
              <RotateCcw size={13} /> 回滚
            </button>
          )}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 border-y border-border-subtle py-3 md:grid-cols-4">
        <Info label="评测套件" value={release.eval_suite} />
        <Info label="数据版本" value={String(provenance.version ?? provenance.dataset_version ?? '—')} />
        <Info label="KB / 语料" value={`${provenance.kb_id ?? '—'} / ${provenance.fixture_set ?? '—'}`} />
        <Info label="模型绑定" value={release.model_binding_fingerprint} mono />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-muted">
        <span>Tool 契约 <code className="font-mono text-text-secondary">{release.tool_contract_fingerprint || '—'}</code></span>
        <span>审批人 {release.approved_by || '—'}</span>
        <a href="/tools" className="inline-flex items-center gap-1 text-accent hover:underline">
          查看 Tool 治理 <ExternalLink size={11} />
        </a>
      </div>

      {Object.keys(release.metrics || {}).length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(release.metrics).slice(0, 6).map(([key, value]) => (
            <span key={key} className="rounded-md bg-slate-50 px-2.5 py-1 text-[11px] text-text-secondary">
              {key}: <strong className="font-mono text-text-primary">{displayValue(value)}</strong>
            </span>
          ))}
        </div>
      )}

      {release.failure_reason && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{release.failure_reason}</span>
        </div>
      )}

      {problems.length > 0 && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>Runtime 尚未完全同步：{problems.join('、')}。请确认热更新完成后再继续。</span>
        </div>
      )}

      {release.runtime_status && (
        <div className="mt-3 flex flex-wrap gap-2" aria-label="Prompt Runtime 进程">
          {release.runtime_status.processes.map(process => (
            <span key={process.instance_id} className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2 py-1 text-[10px] text-text-muted">
              <CheckCircle2 size={11} className={process.status === 'healthy' ? 'text-emerald-500' : 'text-amber-500'} />
              {process.name} · v{process.versions?.[release.prompt_key] ?? '—'}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className={`mt-1 truncate text-xs text-text-primary ${mono ? 'font-mono' : ''}`} title={value}>
        {value || '—'}
      </div>
    </div>
  )
}
