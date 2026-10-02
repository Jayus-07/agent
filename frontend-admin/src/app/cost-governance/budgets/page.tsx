'use client'

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Info, Save, RefreshCw } from 'lucide-react'
import RoleGate from '@/components/auth/RoleGate'
import PageHeader from '@/components/layout/PageHeader'
import { useToast } from '@/components/shared/Toast'
import {
  formatCny,
  getBudgetReconciliation,
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

/** 作用域语义（继承链唯一权威：backend/infra/llm/quota.py::resolve_budget_policies） */
const SCOPE_HINTS: Record<string, string> = {
  user: '单个用户的专属覆盖：优先级最高，配置后覆盖其租户与平台策略',
  tenant: '单个租户的覆盖：对该租户全部用户生效（用户自己无覆盖时）',
  tenant_default: '全租户默认：所有「没有单独配置策略」的租户都继承这一条',
  platform: '平台兜底：只有当「用户、租户、全租户默认」三层都不存在时才会生效——改它通常不影响已有租户的用户',
}

function scopeHint(scopeType: string) {
  return SCOPE_HINTS[scopeType] ?? '自定义预算作用域'
}

/** 列头/术语的悬停解释（原生 title 提示，项目无 tooltip 依赖） */
function InfoTip({ text }: { text: string }) {
  return (
    <span className="ml-1 inline-flex cursor-help align-middle text-text-muted/70 hover:text-accent" title={text}>
      <Info size={12} aria-hidden />
    </span>
  )
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
  const reconciliation = useQuery({ queryKey: ['budget-reconciliation'], queryFn: getBudgetReconciliation, enabled: canAdmin, refetchInterval: 60_000 })

  function beginEdit(policy: BudgetPolicy) {
    setEditing(policy)
    setScopeId(policy.scope_id)
    setDaily(policy.daily_limit_cny)
    setMonthly(policy.monthly_limit_cny)
    setEnforcement(policy.enforcement)
    setReason('')
  }

  function beginUserOverride() {
    setEditing({
      scope_type: 'user', scope_id: '', daily_limit_cny: '3.000000',
      monthly_limit_cny: '50.000000', enforcement: 'hard', timezone: 'Asia/Shanghai',
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
        daily_limit_cny: daily,
        monthly_limit_cny: monthly,
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
          <PageHeader title="预算治理" desc="预算状态由后端权威计算；金额为记账本位币人民币（CNY），美元报价按显式汇率折算。" />

          {/* 继承链说明（2026-10-01 UX 走查）：改策略前先看生效关系，避免
              「改了平台兜底但用户额度没变」这类困惑——上层条目会遮蔽下层 */}
          <div className="mb-6 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-900">
            <Info size={13} className="shrink-0 text-blue-500" aria-hidden />
            <span className="font-medium">额度生效顺序：</span>
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {(['user', 'tenant', 'tenant_default', 'platform'] as const).map((scope, index) => (
                <span key={scope} className="flex items-center gap-2">
                  {index > 0 && <span className="text-blue-300" aria-hidden>›</span>}
                  <span className="rounded-full bg-white/80 px-2 py-0.5 font-medium" title={scopeHint(scope)}>
                    {scope === 'user' ? '用户覆盖' : scope === 'tenant' ? '租户覆盖' : scope === 'tenant_default' ? '全租户默认' : '平台兜底'}
                  </span>
                </span>
              ))}
            </span>
            <span className="text-blue-700/80">左侧优先；下层条目只在上层都不存在时才生效</span>
          </div>

          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <SummaryCard label="总成本（CNY）" value={formatCny(summary.data?.total_cost)} hint={(() => { const c = summary.data?.cost_status_counts; if (!c) return undefined; return `计价 ${c.priced} · 未定价 ${c.unpriced} · 币种未知 ${c.price_unknown}` })()} />
            <SummaryCard label="价格覆盖率" value={`${Math.round((summary.data?.price_coverage_ratio ?? 0) * 100)}%`} alert={(summary.data?.price_coverage_ratio ?? 0) < 1} />
            <SummaryCard label="接近上限" value={String(summary.data?.near_limit_subjects ?? 0)} />
            <SummaryCard label="已阻断主体" value={String(summary.data?.blocked_subjects ?? 0)} alert={(summary.data?.blocked_subjects ?? 0) > 0} />
            <SummaryCard label="未结算预留" value={formatCny(summary.data?.unsettled_reserved)} />
          </div>

          {!canAdmin && (
            <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs text-blue-800">
              当前角色为只读模式。管理员策略、主体和审计明细需要管理员权限。
            </div>
          )}

          {canAdmin && (
            <>
              <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="待对账" action={<RefreshButton onClick={() => reconciliation.refetch()} loading={reconciliation.isFetching} />} />
                <div className="border-b border-slate-50 px-4 py-2.5 text-[11px] text-text-muted">
                  「预占」是每次模型调用前冻结的请求级成本上限。流式用量缺失的调用已按本地估算直接结算（estimated，不入队列）；
                  只有无法估算的调用（失败可能已计费、结算落库失败、滞留超龄）才转入待对账，占额保留到周期结束。
                  对账看比率不看单笔：未决率超阈值时由对账日报任务出告警，下方明细仅作下钻核对。<InfoTip text="占额总额按「预占单」去重统计：一笔预占会同时在用户与租户、日与月维度落多条账本行，去重后才能与下方明细的单据金额对上。" />
                </div>
                <div className="px-4 py-3 text-xs text-text-secondary">
                  队列 {reconciliation.data?.summary.pending_count ?? 0} 笔 · 占额 {formatCny(reconciliation.data?.summary.held_cny)} · 最老滞留 {reconciliation.data?.summary.oldest_age_hours ?? 0} 小时
                  {reconciliation.data?.summary.stale_unswept_count ? ` · 超龄未回收 ${reconciliation.data.summary.stale_unswept_count} 笔` : ''}
                  <span className="ml-2 text-[10px] text-text-muted">回收阈值 {reconciliation.data?.summary.stale_threshold_hours ?? 6} 小时；打开本页即触发幂等回收</span>
                </div>
                <div className="border-t border-slate-50 px-4 py-3 text-xs text-text-secondary">
                  <span className={((reconciliation.data?.summary.needs_review_ratio_window ?? 0) > 0.02) ? 'font-medium text-amber-700' : ''}>
                    未决率（{reconciliation.data?.summary.window_hours ?? 24}h）{((reconciliation.data?.summary.needs_review_ratio_window ?? 0) * 100).toFixed(2)}%
                  </span>
                  {' '}（{reconciliation.data?.summary.needs_review_window ?? 0}/{reconciliation.data?.summary.reservations_total_window ?? 0} 笔预占）
                  {' · '}估算结算 {reconciliation.data?.report?.usage?.estimated_calls ?? 0} 笔（占全部调用 {((reconciliation.data?.report?.derived?.estimated_share ?? 0) * 100).toFixed(2)}%）
                  <span className="ml-2 text-[10px] text-text-muted">未决率持续偏高 = 结算链路或供应商链路疑似系统性异常，按下方原因分布排查</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">预占 ID<InfoTip text="一笔调用产生一个预占单；单内可能在用户/租户、日/月维度有多条账本行" /></th><th className="px-4 py-3">request / 用户 / 租户</th><th className="px-4 py-3 text-right">占额<InfoTip text="该预占单冻结的请求级成本上限（单据金额）" /></th><th className="px-4 py-3">原因<InfoTip text="call_failed_possibly_billed / stream_failed_possibly_billed：调用失败但供应商可能已扣费（对照供应商账单核对）；settle_failed：结算落库失败（用量已在 trace/llm_usage，可按 request_id 下钻）；stale_sweep：滞留超龄自动回收。stream_usage_missing 已改为本地估算结算，正常不再入队" /></th><th className="px-4 py-3">发生时间</th></tr></thead>
                    <tbody>
                      {(reconciliation.data?.items ?? []).map((item) => (
                        <tr key={item.reservation_id} className="border-b border-slate-50">
                          <td className="px-4 py-3 font-mono text-[10px] text-text-muted">{item.reservation_id.slice(0, 8)}</td>
                          <td className="px-4 py-3 font-mono text-[10px] text-text-secondary">{item.request_id || '—'} / {item.user_id} / {item.tenant_id}</td>
                          <td className="px-4 py-3 text-right font-mono text-amber-700">{formatCny(item.reserved_cny)}</td>
                          <td className="px-4 py-3"><span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">{item.review_reason || item.status}</span></td>
                          <td className="px-4 py-3 text-text-muted">{new Date(item.created_at).toLocaleString('zh-CN')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!reconciliation.isLoading && (reconciliation.data?.items ?? []).length === 0 && <EmptyRow text="没有待对账预占——所有调用都已确定结算或释放" />}
                </div>
              </section>

              <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="预算主体" action={<RefreshButton onClick={() => subjects.refetch()} loading={subjects.isFetching} />} />
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">主体<InfoTip text="用户或租户；每行显示它的用量与实际生效的额度上限" /></th><th className="px-4 py-3">显式值 / 继承来源 / 生效值<InfoTip text="显式值=该主体自己配置的策略；继承来源=没有显式策略时实际套用的上层条目；生效值=当前真正执行的日/月上限（按用户覆盖›租户覆盖›全租户默认›平台兜底取第一层）" /></th><th className="px-4 py-3 text-right">日用量 / 上限</th><th className="px-4 py-3 text-right">月用量 / 上限</th><th className="px-4 py-3">执行模式</th></tr></thead>
                    <tbody>
                      {(subjects.data?.items ?? []).map((item) => (
                        <tr key={`${item.scope}:${item.id}`} className="border-b border-slate-50">
                          <td className="px-4 py-3"><div className="font-medium text-text-primary">{item.display_name || item.id}</div><div className="mt-0.5 font-mono text-[10px] text-text-muted" title={scopeHint(item.scope)}>{item.scope}:{item.id}</div></td>
                          <td className="px-4 py-3 text-text-secondary"><div>{item.explicit_policy ? '显式策略' : '未配置显式策略'}</div><div className="mt-0.5 text-[10px] text-text-muted">继承自 {item.policy_source?.label || `${item.policy_source?.scope_type}:${item.policy_source?.scope_id}`}</div><div className="mt-0.5 font-mono text-[11px] font-semibold text-text-primary" title="当前真正执行的上限">生效 {formatCny(item.effective_policy?.daily_limit_cny)} / {formatCny(item.effective_policy?.monthly_limit_cny)}</div></td>
                          <td className={`px-4 py-3 text-right font-mono tabular-nums ${ratioClass(item.daily.ratio)}`}>{formatCny(item.daily.used)} / {formatCny(item.daily.limit)}<div className="text-[10px]">{ratioText(item.daily.ratio)}</div></td>
                          <td className={`px-4 py-3 text-right font-mono tabular-nums ${ratioClass(item.monthly.ratio)}`}>{formatCny(item.monthly.used)} / {formatCny(item.monthly.limit)}<div className="text-[10px]">{ratioText(item.monthly.ratio)}</div></td>
                          <td className="px-4 py-3"><ModeBadge value={item.enforcement} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!subjects.isLoading && (subjects.data?.items ?? []).length === 0 && <EmptyRow text="暂无预算主体数据" />}
                </div>
              </section>

              <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
                <SectionTitle title="策略版本" action={<div className="flex items-center gap-3"><button onClick={beginUserOverride} className="text-[11px] text-accent hover:underline">新建用户覆盖（预填 ¥3 / ¥50）</button><RefreshButton onClick={() => policies.refetch()} loading={policies.isFetching} /></div>} />
                <div className="border-b border-slate-50 px-4 py-2.5 text-[11px] text-text-muted">
                  每行是一条已配置的额度策略；生效顺序见页首说明——改「平台兜底」前先确认目标主体没有更上层的条目。
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">作用域<InfoTip text="user=单用户覆盖；tenant=单租户；tenant_default=所有未配置租户的默认；platform=三层都不存在时的最后兜底。鼠标悬停各行可看对应说明" /></th><th className="px-4 py-3 text-right">日上限</th><th className="px-4 py-3 text-right">月上限</th><th className="px-4 py-3">模式</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
                    <tbody>
                      {(policies.data?.items ?? []).map((policy) => (
                        <tr key={`${policy.scope_type}:${policy.scope_id}`} className="border-b border-slate-50">
                          <td className="px-4 py-3 font-mono text-text-primary"><span title={scopeHint(policy.scope_type)} className="cursor-help underline decoration-dotted decoration-text-muted/40 underline-offset-4">{policy.scope_type}:{policy.scope_id}</span></td><td className="px-4 py-3 text-right font-mono">{formatCny(policy.daily_limit_cny)}</td><td className="px-4 py-3 text-right font-mono">{formatCny(policy.monthly_limit_cny)}</td><td className="px-4 py-3"><ModeBadge value={policy.enforcement} /></td><td className="px-4 py-3 text-text-muted">{policy.updated_at ? new Date(policy.updated_at).toLocaleString('zh-CN') : '—'}</td><td className="px-4 py-3 text-right"><button onClick={() => beginEdit(policy)} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5">编辑</button></td>
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

function SummaryCard({ label, value, alert = false, hint }: { label: string; value: string; alert?: boolean; hint?: string }) {
  return <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card"><div className="text-[11px] text-text-muted">{label}</div><div className={`mt-2 font-mono text-xl font-semibold ${alert ? 'text-red-700' : 'text-text-primary'}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-text-muted">{hint}</div>}</div>
}

function SectionTitle({ title, action }: { title: string; action?: React.ReactNode }) {
  return <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">{title}</h2>{action}</div>
}

function RefreshButton({ onClick, loading }: { onClick: () => void; loading: boolean }) {
  return <button onClick={onClick} className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent"><RefreshCw size={12} className={loading ? 'animate-spin' : ''} />刷新</button>
}

function ModeBadge({ value }: { value: string }) {
  const text = value === 'hard' ? 'hard：超限阻断' : value === 'soft' ? 'soft：仅记录，不阻断' : 'audit：仅审计留痕'
  return <span className={`rounded-full px-2 py-1 text-[10px] ${value === 'hard' ? 'bg-red-50 text-red-700' : value === 'soft' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>{text}</span>
}

function EmptyRow({ text }: { text: string }) {
  return <div className="px-4 py-8 text-center text-xs text-text-muted">{text}</div>
}

function PolicyModal(props: {
  policy: BudgetPolicy; scopeId: string; daily: string; monthly: string; enforcement: 'hard' | 'soft' | 'audit'; reason: string;
  setScopeId: (value: string) => void; setDaily: (value: string) => void; setMonthly: (value: string) => void; setEnforcement: (value: 'hard' | 'soft' | 'audit') => void; setReason: (value: string) => void; onCancel: () => void; onSave: () => void;
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4"><div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"><h2 className="text-sm font-semibold text-text-primary">编辑 {props.policy.scope_type}:{props.scopeId || '新用户'}</h2><p className="mt-1 text-xs text-text-muted">变更立即生效，并保留旧值、新值、操作者、原因和时间。未保存用户覆盖时，用户继续继承租户策略。</p>{(props.policy.scope_type === 'platform' || props.policy.scope_type === 'tenant_default') && <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800"><AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden /><span>{props.policy.scope_type === 'platform' ? '平台兜底位于继承链最末端：只有当用户、租户、全租户默认三层策略都不存在时才会生效。若改它是为了让某个租户/用户生效，请改为配置对应租户或用户覆盖。' : '全租户默认会被每个「没有单独配置策略」的租户继承；已有显式租户策略的租户不受影响。'}</span></div>}{props.policy.scope_type === 'user' && <label className="mt-4 block text-xs text-text-secondary">用户 ID<input value={props.scopeId} onChange={(e) => props.setScopeId(e.target.value)} placeholder="仅填写目录返回的用户 ID" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label>}<div className="mt-4 grid grid-cols-2 gap-3"><label className="text-xs text-text-secondary">日上限 ¥（CNY）<input value={props.daily} onChange={(e) => props.setDaily(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label><label className="text-xs text-text-secondary">月上限 ¥（CNY）<input value={props.monthly} onChange={(e) => props.setMonthly(e.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" /></label></div><label className="mt-3 block text-xs text-text-secondary">执行模式<select value={props.enforcement} onChange={(e) => props.setEnforcement(e.target.value as 'hard' | 'soft' | 'audit')} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"><option value="hard">hard：超限阻断</option><option value="soft">soft：仅记录与阈值告警，不阻断</option><option value="audit">audit：仅审计留痕</option></select></label><label className="mt-3 block text-xs text-text-secondary">变更原因<textarea value={props.reason} onChange={(e) => props.setReason(e.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：本月营销活动预算调整" /></label><div className="mt-5 flex justify-end gap-2"><button onClick={props.onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button><button onClick={props.onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white"><Save size={13} />保存</button></div></div></div>
}
