'use client'

import { CheckCircle2, FileCheck2, LockKeyhole } from 'lucide-react'
import type { DatasetVersionDetail as DatasetVersionDetailModel } from '@/api/evaluation'

export default function DatasetVersionDetail({ dataset }: { dataset: DatasetVersionDetailModel }) {
  const metadata = dataset.metadata || {}
  const kbId = String(metadata.kb_id || '—')
  const fixtureSet = String(metadata.fixture_set || '—')
  return (
    <section className="rounded-xl border border-border-subtle bg-surface-base p-4" aria-label="评测集版本详情">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <FileCheck2 size={15} className="text-accent" />
            <h2 className="text-sm font-semibold text-text-primary">不可变版本 v{dataset.version}</h2>
            {dataset.immutable && <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700"><LockKeyhole size={10} /> immutable</span>}
          </div>
          <p className="mt-1 text-[11px] text-text-muted">{dataset.dataset_id} · owner {dataset.owner} · {dataset.case_count} 条用例</p>
        </div>
        <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700"><CheckCircle2 size={13} /> {dataset.review_status}</span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 border-y border-border-subtle py-3 md:grid-cols-4">
        <Info label="内容 hash" value={dataset.content_hash} mono />
        <Info label="KB" value={kbId} />
        <Info label="Fixture" value={fixtureSet} />
        <Info label="校验通过" value={String(dataset.coverage?.verified_count ?? '—')} />
      </div>

      <div className="mt-3">
        <div className="text-[10px] text-text-muted">Suite 归属</div>
        <div className="mt-1 flex flex-wrap gap-2">
          {dataset.suite_membership.length === 0 ? (
            <span className="text-[11px] text-text-muted">暂无 Suite 关联</span>
          ) : dataset.suite_membership.map(suite => (
            <span key={String(suite.name)} className="rounded-md bg-accent/10 px-2 py-1 text-[11px] text-accent">
              {String(suite.name)} · {String(suite.case_count ?? '—')} cases
            </span>
          ))}
        </div>
      </div>

      {dataset.case_diff.added.length > 0 && (
        <p className="mt-3 text-[11px] text-text-secondary">本版本新增：{dataset.case_diff.added.join('、')}</p>
      )}
    </section>
  )
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="min-w-0"><div className="text-[10px] text-text-muted">{label}</div><div className={`mt-1 truncate text-xs text-text-primary ${mono ? 'font-mono' : ''}`} title={value}>{value || '—'}</div></div>
}
