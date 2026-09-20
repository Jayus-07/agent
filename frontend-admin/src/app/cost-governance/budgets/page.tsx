'use client'

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Save, RefreshCw } from 'lucide-react'
import RoleGate from '@/components/auth/RoleGate'
import PageHeader from '@/components/layout/PageHeader'
import { useToast } from '@/components/shared/Toast'
import {
  formatUsd,
  getBudgetSummary,
  listBudgetEvents,
  listBudgetPolicyAudit,
  listBudgetPolicies,
  listBudgetSubjects,
  saveBudgetPolicy,
  type BudgetPolicy,
} from '@/api/budgets'
import { atLeast } from '@/lib/auth'

function ratioText(value: number) {
  return `${Math.round(value * 100)}%`
}

function ratioClass(value: number) {
  return value >= 1 ? 'text-red-700' : value >= 0.8 ? 'text-amber-700' : 'text-emerald-700'
}

export default function BudgetGovernancePage() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const canAdmin = atLeast('admin')
  const [editing, setEditing] = useState<BudgetPolicy | null>(null)
  const [scopeId, setScopeId] = useState('')
  const [daily, setDaily] = useState('')
  const [monthly, setMonthly] = useState('')
  const [enforcement, setEnforcement] = useState<'hard' | 'soft' | 'audit'>('hard')
  const [reason, setReason] = useState('')

  const summary = useQuery({ queryKey: ['budget-summary'], queryFn: getBudgetSummary, refetchInterval: 60_000 })
  const subjects = useQuery({ queryKey: ['budget-subjects'], queryFn: () => listBudgetSubjects(), enabled: canAdmin })
  const policies = useQuery({ queryKey: ['budget-policies'], queryFn: listBudgetPolicies, enabled: canAdmin })
  const events = useQuery({ queryKey: ['budget-events'], queryFn: listBudgetEvents, enabled: canAdmin })
  const audit = useQuery({ queryKey: ['budget-policy-audit'], queryFn: listBudgetPolicyAudit, enabled: canAdmin })

  function beginEdit(policy: BudgetPolicy) {
    setEditing(policy)
    setScopeId(policy.scope_id)
    setDaily(policy.daily_limit_usd)
    setMonthly(policy.monthly_limit_usd)
    setEnforcement(policy.enforcement)
    setReason('')
  }

  function beginUserOverride() {
    setEditing({
      scope_type: 'user', scope_id: '', daily_limit_usd: '3.000000',
      monthly_limit_usd: '50.000000', enforcement: 'hard', timezone: 'Asia/Shanghai',
      audit_exempt: false,
    })
    setScopeId('')
    setDaily('3.000000')
    setMonthly('50.000000')
    setEnforcement('hard')
    setReason('')
  }

  async function submitPolicy() {
    if (!editing || !scopeId.trim()) {
      toast.error('请填写用户 ID')
      return
    }
    if (!reason.trim()) {
      toast.error('预算策略变更必须填写原因')
      return
    }
    try {
      await saveBudgetPolicy(editing.scope_type, scopeId.trim(), {
        daily_limit_usd: daily,
        monthly_limit_usd: monthly,
        enforcement,
        audit_exempt: editing.audit_exempt,
        reason: reason.trim(),
        expected_updated_at: editing.updated_at,
      })
      toast.success('预算策略已保存并写入审计')
      setEditing(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['budget-policies'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-summary'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-events'] }),
      ])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '预算策略保存失败')
    }
  }

  return (
    <RoleGate minRole="viewer" pageName="预算治理">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-6 py-8">
          <PageHeader title="预算治理" desc="预算状态由后端权威计算；此处只展示 USD 结果与受控策略变更。" />

          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <SummaryCard label="总成本" value={formatUsd(summary.data?.total_cost_usd)} />
            <SummaryCard label="价格覆盖率" value={`${Math.round((summary.data?.price_coverage_ratio ?? 0) * 100)}%`} alert={(summary.data?.price_coverage_ratio ?? 0) < 1} />
            <SummaryCard label="接近上限" value={String(summary.data?.near_limit_subjects ?? 0)} />
            <SummaryCard label="已阻断主体" value={String(summary.data?.blocked_subjects ?? 0)} alert={(summary.data?.blocked_subjects ?? 0) > 0} />
            <SummaryCard label="未结算预留" value={formatUsd(summary.data?.unsettled_reserved_usd)} />
          </div>

          {!canAdmin && (
            <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs text-blue-800">
              当前角色为只读模式。管理员策略、主体和审计明细需要管理员权限。
            </div>
          )}

          {canAdmin && (
            <>
              <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="预算主体" action={<RefreshButton onClick={() => subjects.refetch()} loading={subjects.isFetching} />} />
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">主体</th><th className="px-4 py-3">显式值 / 继承来源 / 生效值</th><th className="px-4 py-3 text-right">日用量 / 上限</th><th className="px-4 py-3 text-right">月用量 / 上限</th><th className="px-4 py-3">执行模式</th></tr></thead>
                    <tbody>
                      {(subjects.data?.items ?? []).map((item) => (
                        <tr key={`${item.scope}:${item.id}`} className="border-b border-slate-50">
                          <td className="px-4 py-3"><div className="font-medium text-text-primary">{item.display_name || item.id}</div><div className="mt-0.5 font-mono text-[10px] text-text-muted">{item.scope}:{item.id}</div></td>
                          <td className="px-4 py-3 text-text-secondary"><div>{item.explicit_policy ? '显式策略' : '未配置显式策略'}</div><div className="mt-0.5 text-[10px] text-text-muted">继承自 {item.policy_source?.label || `${item.policy_source?.scope_type}:${item.policy_source?.scope_id}`}</div><div className="mt-0.5 font-mono text-[10px] text-text-muted">生效 {formatUsd(item.effective_policy?.daily_limit_usd)} / {formatUsd(item.effective_policy?.monthly_limit_usd)}</div></td>
                          <td className={`px-4 py-3 text-right font-mono tabular-nums ${ratioClass(item.daily.ratio)}`}>{formatUsd(item.daily.used)} / {formatUsd(item.daily.limit)}<div className="text-[10px]">{ratioText(item.daily.ratio)}</div></td>
                          <td className={`px-4 py-3 text-right font-mono tabular-nums ${ratioClass(item.monthly.ratio)}`}>{formatUsd(item.monthly.used)} / {formatUsd(item.monthly.limit)}<div className="text-[10px]">{ratioText(item.monthly.ratio)}</div></td>
                          <td className="px-4 py-3"><ModeBadge value={item.enforcement} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!subjects.isLoading && (subjects.data?.items ?? []).length === 0 && <EmptyRow text="暂无预算主体数据" />}
                </div>
              </section>

              <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="策略版本" action={<div className="flex items-center gap-3"><button onClick={beginUserOverride} className="text-[11px] text-accent hover:underline">新建用户覆盖（预填 3 / 50 USD）</button><RefreshButton onClick={() => policies.refetch()} loading={policies.isFetching} /></div>} />
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">作用域</th><th className="px-4 py-3 text-right">日上限</th><th className="px-4 py-3 text-right">月上限</th><th className="px-4 py-3">模式</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
                    <tbody>
                      {(policies.data?.items ?? []).map((policy) => (
                        <tr key={`${policy.scope_type}:${policy.scope_id}`} className="border-b border-slate-50">
                          <td className="px-4 py-3 font-mono text-text-primary">{policy.scope_type}:{policy.scope_id}</td><td className="px-4 py-3 text-right font-mono">{formatUsd(policy.daily_limit_usd)}</td><td className="px-4 py-3 text-right font-mono">{formatUsd(policy.monthly_limit_usd)}</td><td className="px-4 py-3"><ModeBadge value={policy.enforcement} /></td><td className="px-4 py-3 text-text-muted">{policy.updated_at ? new Date(policy.updated_at).toLocaleString('zh-CN') : '—'}</td><td className="px-4 py-3 text-right"><button onClick={() => beginEdit(policy)} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5">编辑</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="预算审计事件" action={<RefreshButton onClick={() => events.refetch()} loading={events.isFetching} />} />
                <div className="divide-y divide-slate-100">
                  {(events.data?.items ?? []).map((event, index) => <div key={`${event.scope_type}:${event.scope_id}:${event.period_type}:${event.threshold}:${index}`} className="flex items-center justify-between px-4 py-3 text-xs"><span className="font-mono text-text-primary">{event.scope_type}:{event.scope_id}</span><span className="text-text-secondary">{event.period_type === 'day' ? '日' : '月'}额度达到 {Math.round(event.threshold * 100)}%</span><time className="text-text-muted">{new Date(event.created_at).toLocaleString('zh-CN')}</time></div>)}
                  {(events.data?.items ?? []).length === 0 && <EmptyRow text="暂无阈值事件" />}
                </div>
              </section>
              <section className="mt-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="策略变更审计" action={<RefreshButton onClick={() => audit.refetch()} loading={audit.isFetching} />} />
                <div className="divide-y divide-slate-100">
                  {(audit.data?.items ?? []).map((item, index) => <div key={`${item.scope_type}:${item.scope_id}:${item.created_at}:${index}`} className="grid gap-2 px-4 py-3 text-xs md:grid-cols-[180px_1fr_150px]"><div><div className="font-mono text-text-primary">{item.scope_type}:{item.scope_id}</div><div className="mt-1 text-[10px] text-text-muted">{new Date(item.created_at).toLocaleString('zh-CN')}</div></div><div><div className="text-text-secondary">{item.reason}</div><div className="mt-1 text-[10px] text-text-muted">修改前 {item.before_value ? JSON.stringify(item.before_value) : '无'} → 修改后 {JSON.stringify(item.after_value)}</div></div><div className="font-mono text-[11px] text-text-muted">{item.updated_by}</div></div>)}
                  {(audit.data?.items ?? []).length === 0 && <EmptyRow text="暂无策略变更审计" />}
                </div>
              </section>
            </>
          )}

          {editing && <PolicyModal policy={editing} scopeId={scopeId} daily={daily} monthly={monthly} enforcement={enforcement} reason={reason} setScopeId={setScopeId} setDaily={setDaily} setMonthly={setMonthly} setEnforcement={setEnforcement} setReason={setReason} onCancel={() => setEditing(null)} onSave={submitPolicy} />}
        </div>
      </div>
    </RoleGate>
  )
}

function SummaryCard({ label, value, alert = false }: { label: string; value: string; alert?: boolean }) {
  return <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card"><div className="text-[11px] text-text-muted">{label}</div><div className={`mt-2 font-mono text-xl font-semibold ${alert ? 'text-red-700' : 'text-text-primary'}`}>{value}</div></div>
}

function SectionTitle({ title, action }: { title: string; action?: React.ReactNode }) {
  return <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">{title}</h2>{action}</div>
}

function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return <button onClick={onClick} className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent"><RefreshCw size={12} className={loading ? 'animate-spin' : ''} />刷新</button>
}

function ModeBadge({ value }: { value: string }) {
  return <span className={`rounded-full px-2 py-1 text-[10px] ${value === 'hard' ? 'bg-red-50 text-red-700' : value === 'soft' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>{value}</span>
}

function EmptyRow({ text }: { text: string }) {
  return <div className="px-4 py-8 text-center text-xs text-text-muted">{text}</div>
}

function PolicyModal(props: {
  policy: BudgetPolicy; scopeId: string; daily: string; monthly: string; enforcement: 'hard' | 'soft' | 'audit'; reason: string;
  setScopeId: (value: string) => void; setDaily: (value: string) => void; setMonthly: (value: string) => void; setEnforcement: (value: 'hard' | 'soft' | 'audit') => void; setReason: (value: string) => void; onCancel: () => void; onSave: () => void;
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4"><div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"><h2 className="text-sm font-semibold text-text-primary">编辑 {props.policy.scope_type}:{props.scopeId || '新用户'}</h2><p className="mt-1 text-xs text-text-muted">变更立即生效，并保留旧值、新值、操作者、原因和时间。未保存用户覆盖时，用户继续继承租户策略。</p>{props.policy.scope_type === 'user' && <label className="mt-4 block text-xs text-text-secondary">用户 ID<input value={props.scopeId} onChange={(e) => props.setScopeId(e.target.value)} placeholder="仅填写目录返回的用户 ID" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label>}<div className="mt-4 grid grid-cols-2 gap-3"><label className="text-xs text-text-secondary">日上限 USD<input value={props.daily} onChange={(e) => props.setDaily(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label><label className="text-xs text-text-secondary">月上限 USD<input value={props.monthly} onChange={(e) => props.setMonthly(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label></div><label className="mt-3 block text-xs text-text-secondary">执行模式<select value={props.enforcement} onChange={(e) => props.setEnforcement(e.target.value as 'hard' | 'soft' | 'audit')} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="hard">hard：超限阻断</option><option value="soft">soft：超限告警</option><option value="audit">audit：仅审计</option></select></label><label className="mt-3 block text-xs text-text-secondary">变更原因<textarea value={props.reason} onChange={(e) => props.setReason(e.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：本月营销活动预算调整" /></label><div className="mt-5 flex justify-end gap-2"><button onClick={props.onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button><button onClick={props.onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white"><Save size={13} />保存</button></div></div></div>
}
