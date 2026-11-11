'use client'

/**
 * 主体与策略面板（2026-11-10 Tab 化）：继承链说明 + 预算主体表
 * （搜索/测试数据开关/分页）+ 策略版本表 + Tool 约束。
 *
 * 测试/评测主体默认排除（与待对账队列 include_test 同口径），打开开关才
 * 显示 test/eval/probe 等前缀主体；分页为客户端分页（后端单次返回全部
 * 过滤后主体，量级为正式用户/租户数）。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Info } from 'lucide-react'

import { useToast } from '@/components/shared/Toast'
import {
  formatCny,
  listBudgetPolicies,
  listBudgetSubjects,
  saveBudgetPolicy,
  type BudgetPolicy,
} from '@/api/budgets'
import {
  EmptyRow,
  InfoTip,
  ModeBadge,
  PolicyModal,
  RefreshButton,
  SectionTitle,
  ratioClass,
  ratioText,
  scopeHint,
} from '../shared'
import ToolConstraintsSection from '../ToolConstraintsSection'

const SUBJECTS_PAGE_SIZE = 15

export default function BudgetSubjectsPanel() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [subjectQuery, setSubjectQuery] = useState('')
  const [includeTestSubjects, setIncludeTestSubjects] = useState(false)
  const [subjectPage, setSubjectPage] = useState(1)
  const [editing, setEditing] = useState<BudgetPolicy | null>(null)
  const [scopeId, setScopeId] = useState('')
  const [daily, setDaily] = useState('')
  const [monthly, setMonthly] = useState('')
  const [enforcement, setEnforcement] = useState<'hard' | 'soft' | 'audit'>('hard')
  const [reason, setReason] = useState('')
  // 任务 9：域额度策略（''=总额度；main/travel/customer_service=域额度）
  const [policyDomain, setPolicyDomain] = useState('')

  const subjects = useQuery({
    queryKey: ['budget-subjects', includeTestSubjects],
    queryFn: () => listBudgetSubjects({ includeTest: includeTestSubjects }),
    refetchInterval: 60_000,
  })
  const policies = useQuery({ queryKey: ['budget-policies'], queryFn: listBudgetPolicies })

  const filteredSubjects = (subjects.data?.items ?? []).filter((item) => {
    const q = subjectQuery.trim().toLowerCase()
    if (!q) return true
    return (
      item.id.toLowerCase().includes(q)
      || (item.display_name || '').toLowerCase().includes(q)
      || `${item.scope}:${item.id}`.toLowerCase().includes(q)
    )
  })
  const pageCount = Math.max(1, Math.ceil(filteredSubjects.length / SUBJECTS_PAGE_SIZE))
  const safePage = Math.min(subjectPage, pageCount)
  const pagedSubjects = filteredSubjects.slice((safePage - 1) * SUBJECTS_PAGE_SIZE, safePage * SUBJECTS_PAGE_SIZE)

  function beginEdit(policy: BudgetPolicy) {
    setEditing(policy)
    setScopeId(policy.scope_id)
    setDaily(policy.daily_limit_cny)
    setMonthly(policy.monthly_limit_cny)
    setEnforcement(policy.enforcement)
    setPolicyDomain(policy.domain || '')
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
    setPolicyDomain('')
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
        domain: policyDomain,
      })
      toast.success('预算策略已保存并写入审计')
      setEditing(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['budget-policies'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-summary'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-events'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-subjects'] }),
      ])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '预算策略保存失败')
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
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

      <section className="mb-6 rounded-xl border border-black/5 bg-white shadow-card">
        <SectionTitle title="预算主体" action={<div className="flex items-center gap-3"><input value={subjectQuery} onChange={(event) => { setSubjectQuery(event.target.value); setSubjectPage(1) }} placeholder="搜索用户 / 租户 ID" className="w-44 rounded-lg border border-black/10 px-2 py-1.5 text-[11px]" /><label className="flex items-center gap-1 text-[11px] text-text-secondary"><input type="checkbox" checked={includeTestSubjects} onChange={(event) => { setIncludeTestSubjects(event.target.checked); setSubjectPage(1) }} />测试数据</label><RefreshButton onClick={() => subjects.refetch()} loading={subjects.isFetching} /></div>} />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">主体<InfoTip text="用户或租户；每行显示它的用量与实际生效的额度上限" /></th><th className="px-4 py-3">显式值 / 继承来源 / 生效值<InfoTip text="显式值=该主体自己配置的策略；继承来源=没有显式策略时实际套用的上层条目；生效值=当前真正执行的日/月上限（按用户覆盖›租户覆盖›全租户默认›平台兜底取第一层）" /></th><th className="px-4 py-3 text-right">日用量 / 上限</th><th className="px-4 py-3 text-right">月用量 / 上限</th><th className="px-4 py-3">执行模式</th></tr></thead>
            <tbody>
              {pagedSubjects.map((item) => (
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
          {!subjects.isLoading && (subjects.data?.items ?? []).length === 0 && <EmptyRow text="暂无预算主体数据（打开「测试数据」可查看评测/联调主体）" />}
          {!subjects.isLoading && filteredSubjects.length === 0 && (subjects.data?.items ?? []).length > 0 && <EmptyRow text="没有匹配搜索条件的主体" />}
          {filteredSubjects.length > 0 && (
            <div className="flex items-center justify-between px-4 py-2.5 text-[11px] text-text-muted">
              <span>共 {filteredSubjects.length} 个主体{includeTestSubjects ? '（含测试数据）' : '（已排除测试数据）'}</span>
              {pageCount > 1 && (
                <div className="flex items-center gap-2">
                  <button disabled={safePage <= 1} onClick={() => setSubjectPage(safePage - 1)} className="rounded-lg border border-black/10 px-2 py-1 disabled:opacity-40">上一页</button>
                  <span>第 {safePage} / {pageCount} 页</span>
                  <button disabled={safePage >= pageCount} onClick={() => setSubjectPage(safePage + 1)} className="rounded-lg border border-black/10 px-2 py-1 disabled:opacity-40">下一页</button>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-black/5 bg-white shadow-card">
        <SectionTitle title="策略版本" action={<div className="flex items-center gap-3"><button onClick={beginUserOverride} className="text-[11px] text-accent hover:underline">新建用户覆盖（预填 ¥3 / ¥50）</button><RefreshButton onClick={() => policies.refetch()} loading={policies.isFetching} /></div>} />
        <div className="border-b border-slate-50 px-4 py-2.5 text-[11px] text-text-muted">
          每行是一条已配置的额度策略；生效顺序见上方说明——改「平台兜底」前先确认目标主体没有更上层的条目。
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">作用域<InfoTip text="user=单用户覆盖；tenant=单租户；tenant_default=所有未配置租户的默认；platform=三层都不存在时的最后兜底。鼠标悬停各行可看对应说明" /></th><th className="px-4 py-3">域<InfoTip text="任务 9 域额度：'—'为总额度策略；main=主助手（含 RAG/SQL）；travel=旅游；customer_service=客服。域策略与总额度策略独立生效（4 道闸）" /></th><th className="px-4 py-3 text-right">日上限</th><th className="px-4 py-3 text-right">月上限</th><th className="px-4 py-3">模式</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
            <tbody>
              {(policies.data?.items ?? []).map((policy) => (
                <tr key={`${policy.scope_type}:${policy.scope_id}:${policy.domain || ''}`} className="border-b border-slate-50">
                  <td className="px-4 py-3 font-mono text-text-primary"><span title={scopeHint(policy.scope_type)} className="cursor-help underline decoration-dotted decoration-text-muted/40 underline-offset-4">{policy.scope_type}:{policy.scope_id}</span></td><td className="px-4 py-3">{policy.domain ? <span className="rounded-full bg-blue-50 px-2 py-1 text-[10px] text-blue-700">{policy.domain}</span> : <span className="text-[10px] text-text-muted">—</span>}</td><td className="px-4 py-3 text-right font-mono">{formatCny(policy.daily_limit_cny)}</td><td className="px-4 py-3 text-right font-mono">{formatCny(policy.monthly_limit_cny)}</td><td className="px-4 py-3"><ModeBadge value={policy.enforcement} /></td><td className="px-4 py-3 text-text-muted">{policy.updated_at ? new Date(policy.updated_at).toLocaleString('zh-CN') : '—'}</td><td className="px-4 py-3 text-right"><button onClick={() => beginEdit(policy)} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5">编辑</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {(policies.data?.items ?? []).length === 0 && !policies.isLoading && <EmptyRow text="暂无已配置策略" />}
        </div>
      </section>

      <div className="mt-6">
        <ToolConstraintsSection />
      </div>

      {editing && <PolicyModal policy={editing} scopeId={scopeId} daily={daily} monthly={monthly} enforcement={enforcement} reason={reason} policyDomain={policyDomain} setScopeId={setScopeId} setDaily={setDaily} setMonthly={setMonthly} setEnforcement={setEnforcement} setReason={setReason} setPolicyDomain={setPolicyDomain} onCancel={() => setEditing(null)} onSave={submitPolicy} />}
    </div>
  )
}
