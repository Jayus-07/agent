'use client'

import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from 'lucide-react'
import type { DriftItem } from '@/types/modelConfig'

export default function DriftTab({ items }: { items: DriftItem[] }) {
  const critical = items.filter((item) => item.severity === 'critical')
  const warnings = items.filter((item) => item.severity === 'warn')
  return <section className="rounded-xl border border-black/5 bg-white shadow-card"><div className="border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">体检与漂移</h2><p className="mt-1 text-[11px] text-text-muted">每一项都给出来源与处理建议；“无法判定”不会伪装成通过。</p></div>{!items.length ? <div className="flex items-center gap-2 px-4 py-10 text-sm text-emerald-700"><CheckCircle2 size={17} />当前检查项全部通过</div> : <div className="divide-y divide-slate-100">{items.map((item, index) => <DriftRow key={`${item.kind}:${item.subject}:${index}`} item={item} />)}</div>}<div className="border-t border-slate-100 px-4 py-3 text-[10px] text-text-muted">严重 {critical.length} · 警告 {warnings.length} · 信息 {items.length - critical.length - warnings.length}</div></section>
}

function DriftRow({ item }: { item: DriftItem }) {
  const tone = item.severity === 'critical' ? { box: 'bg-red-50 text-red-800', icon: <ShieldAlert size={15} />, label: '严重' } : item.severity === 'warn' ? { box: 'bg-amber-50 text-amber-800', icon: <AlertTriangle size={15} />, label: '警告' } : { box: 'bg-blue-50 text-blue-800', icon: <Info size={15} />, label: '信息' }
  return <div className="flex gap-3 px-4 py-3"><div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${tone.box}`}>{tone.icon}</div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2 text-xs font-medium text-text-primary"><span>{tone.label}</span><span className="font-mono text-[10px] text-text-muted">{item.kind} · {item.subject}</span></div><p className="mt-1 text-xs text-text-secondary">{item.message}</p>{item.hint && <p className="mt-1 text-[11px] text-text-muted">建议：{item.hint}</p>}</div></div>
}
