'use client'

/**
 * 预算治理页共享组件（2026-11-10 Tab 化拆分）：page.tsx 拆成
 * overview/reconciliation/subjects/audit 四个面板后，卡片、表格骨架、
 * 弹窗等跨面板复用的部件收敛到本文件，避免面板间互相 import。
 */
import type { ReactNode } from 'react'
import { AlertTriangle, Info, RefreshCw, Save } from 'lucide-react'

import { type BudgetPolicy } from '@/api/budgets'

/** 作用域语义（继承链唯一权威：backend/infra/llm/quota.py::resolve_budget_policies） */
const SCOPE_HINTS: Record<string, string> = {
  user: '单个用户的专属覆盖：优先级最高，配置后覆盖其租户与平台策略',
  tenant: '单个租户的覆盖：对该租户全部用户生效（用户自己无覆盖时）',
  tenant_default: '全租户默认：所有「没有单独配置策略」的租户都继承这一条',
  platform: '平台兜底：只有当「用户、租户、全租户默认」三层都不存在时才会生效——改它通常不影响已有租户的用户',
}

export function scopeHint(scopeType: string) {
  return SCOPE_HINTS[scopeType] ?? '未知作用域'
}

export function ratioText(value: number) {
  return `${Math.round(value * 100)}%`
}

export function ratioClass(value: number) {
  return value >= 1 ? 'text-red-700' : value >= 0.8 ? 'text-amber-700' : 'text-emerald-700'
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span className="group relative ml-1 inline-flex cursor-help align-middle" role="img" aria-label={text}>
      <Info size={12} className="text-text-muted" aria-hidden />
      <span className="pointer-events-none absolute left-1/2 top-full z-20 mt-1 hidden w-64 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-[10px] leading-4 text-white group-hover:block">{text}</span>
    </span>
  )
}

export function SummaryCard({ label, value, alert = false, hint }: { label: string; value: string; alert?: boolean; hint?: string }) {
  return <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card"><div className="text-[11px] text-text-muted">{label}</div><div className={`mt-2 font-mono text-xl font-semibold ${alert ? 'text-red-700' : 'text-text-primary'}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-text-muted">{hint}</div>}</div>
}

export function SectionTitle({ title, action }: { title: string; action?: ReactNode }) {
  return <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">{title}</h2>{action}</div>
}

export function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return <button onClick={onClick} className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent"><RefreshCw size={12} className={loading ? 'animate-spin' : ''} />刷新</button>
}

export function ModeBadge({ value }: { value: string }) {
  const text = value === 'hard' ? 'hard：超限阻断' : value === 'soft' ? 'soft：仅记录，不阻断' : 'audit：仅审计留痕'
  return <span className={`rounded-full px-2 py-1 text-[10px] ${value === 'hard' ? 'bg-red-50 text-red-700' : value === 'soft' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>{text}</span>
}

export function EmptyRow({ text }: { text: string }) {
  return <div className="px-4 py-8 text-center text-xs text-text-muted">{text}</div>
}

export function PolicyModal(props: {
  policy: BudgetPolicy; scopeId: string; daily: string; monthly: string; enforcement: 'hard' | 'soft' | 'audit'; reason: string;
  setScopeId: (value: string) => void; setDaily: (value: string) => void; setMonthly: (value: string) => void; setEnforcement: (value: 'hard' | 'soft' | 'audit') => void; setReason: (value: string) => void; onCancel: () => void; onSave: () => void;
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4"><div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"><h2 className="text-sm font-semibold text-text-primary">编辑 {props.policy.scope_type}:{props.scopeId || '新用户'}</h2><p className="mt-1 text-xs text-text-muted">变更立即生效，并保留旧值、新值、操作者、原因和时间。未保存用户覆盖时，用户继续继承租户策略。</p>{(props.policy.scope_type === 'platform' || props.policy.scope_type === 'tenant_default') && <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800"><AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden /><span>{props.policy.scope_type === 'platform' ? '平台兜底位于继承链最末端：只有当用户、租户、全租户默认三层策略都不存在时才会生效。若改它是为了让某个租户/用户生效，请改为配置对应租户或用户覆盖。' : '全租户默认会被每个「没有单独配置策略」的租户继承；已有显式租户策略的租户不受影响。'}</span></div>}{props.policy.scope_type === 'user' && <label className="mt-4 block text-xs text-text-secondary">用户 ID<input value={props.scopeId} onChange={(e) => props.setScopeId(e.target.value)} placeholder="仅填写目录返回的用户 ID" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label>}<div className="mt-4 grid grid-cols-2 gap-3"><label className="text-xs text-text-secondary">日上限 ¥（CNY）<input value={props.daily} onChange={(e) => props.setDaily(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label><label className="text-xs text-text-secondary">月上限 ¥（CNY）<input value={props.monthly} onChange={(e) => props.setMonthly(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label></div><label className="mt-3 block text-xs text-text-secondary">执行模式<select value={props.enforcement} onChange={(e) => props.setEnforcement(e.target.value as 'hard' | 'soft' | 'audit')} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="hard">hard：超限阻断</option><option value="soft">soft：仅记录与阈值告警，不阻断</option><option value="audit">audit：仅审计留痕</option></select></label><label className="mt-3 block text-xs text-text-secondary">变更原因<textarea value={props.reason} onChange={(e) => props.setReason(e.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：本月营销活动预算调整" /></label><div className="mt-5 flex justify-end gap-2"><button onClick={props.onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button><button onClick={props.onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white"><Save size={13} />保存</button></div></div></div>
}
