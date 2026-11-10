'use client'

/**
 * 审计面板（2026-11-10 Tab 化）：预算阈值事件 + 策略变更审计。
 */
import { useQuery } from '@tanstack/react-query'

import { listBudgetEvents, listBudgetPolicyAudit } from '@/api/budgets'
import { EmptyRow, RefreshButton, SectionTitle } from '../shared'

export default function BudgetAuditPanel() {
  const events = useQuery({ queryKey: ['budget-events'], queryFn: listBudgetEvents, refetchInterval: 60_000 })
  const audit = useQuery({ queryKey: ['budget-policy-audit'], queryFn: listBudgetPolicyAudit, refetchInterval: 60_000 })

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
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
    </div>
  )
}
