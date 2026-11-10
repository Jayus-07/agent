'use client'

/**
 * 概览面板（2026-11-10 Tab 化）：汇总卡 + 月度对账。原单页首屏的前半段，
 * 待对账/主体/审计拆到同目录其余面板。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { useToast } from '@/components/shared/Toast'
import {
  downloadBudgetMonthlyCsv,
  formatCny,
  getBudgetMonthlyReconciliation,
  getBudgetSummary,
} from '@/api/budgets'
import { EmptyRow, InfoTip, RefreshButton, SectionTitle, SummaryCard } from '../shared'

export default function BudgetOverviewPanel({ canAdmin }: { canAdmin: boolean }) {
  const toast = useToast()
  const [csvBusy, setCsvBusy] = useState(false)

  const summary = useQuery({ queryKey: ['budget-summary'], queryFn: getBudgetSummary, refetchInterval: 60_000 })
  // 月度对账（路线图第 4 块 PR-A）：按月聚合 usage 成本 / 预算月行 / 结算形态
  const monthlyRecon = useQuery({
    queryKey: ['budget-monthly-recon'],
    queryFn: () => getBudgetMonthlyReconciliation(6),
    enabled: canAdmin, refetchInterval: 300_000,
  })

  async function exportMonthlyCsv() {
    setCsvBusy(true)
    try {
      await downloadBudgetMonthlyCsv(6)
      toast.success('月度对账 CSV 已导出')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '月度对账 CSV 导出失败')
    } finally {
      setCsvBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <SummaryCard label="总成本（CNY）" value={formatCny(summary.data?.total_cost)} hint={(() => { const c = summary.data?.cost_status_counts; if (!c) return undefined; return `计价 ${c.priced} · 未定价 ${c.unpriced} · 币种未知 ${c.price_unknown}` })()} />
        <SummaryCard label="价格覆盖率" value={`${Math.round((summary.data?.price_coverage_ratio ?? 0) * 100)}%`} alert={(summary.data?.price_coverage_ratio ?? 0) < 1} />
        <SummaryCard label="接近上限" value={String(summary.data?.near_limit_subjects ?? 0)} />
        <SummaryCard label="已阻断主体" value={String(summary.data?.blocked_subjects ?? 0)} alert={(summary.data?.blocked_subjects ?? 0) > 0} />
        <SummaryCard label="未结算预留" value={formatCny(summary.data?.unsettled_reserved)} />
      </div>

      {/* 月度对账：按月 usage 成本 / 结算形态分布 + CSV 导出 */}
      <section className="rounded-xl border border-black/5 bg-white shadow-card">
        <SectionTitle
          title="月度对账"
          action={
            canAdmin ? (
              <div className="flex items-center gap-3">
                <button onClick={exportMonthlyCsv} disabled={csvBusy} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] disabled:opacity-50">导出 CSV</button>
                <RefreshButton onClick={() => monthlyRecon.refetch()} loading={monthlyRecon.isFetching} />
              </div>
            ) : undefined
          }
        />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-[10px] text-text-muted">
                <th className="px-4 py-3">月份</th>
                <th className="px-4 py-3 text-right">usage 成本（CNY）<InfoTip text="llm_usage 当月 CNY 成本合计（cost_cny_sql_expr 唯一折算口径）；按预算时区（Asia/Shanghai）分月" /></th>
                <th className="px-4 py-3 text-right">调用数</th>
                <th className="px-4 py-3 text-right">精确结算<InfoTip text="settle_kind=''：按实际用量精确结算的预占单" /></th>
                <th className="px-4 py-3 text-right">估算结算<InfoTip text="settle_kind='estimated'：按预占上限保守估算结算（小额未知用量），设计内口径" /></th>
                <th className="px-4 py-3 text-right">待对账核销<InfoTip text="resolution 非空：released / written_off / archived / settled_actual 的人工核销单" /></th>
                <th className="px-4 py-3 text-right">预算月行入账<InfoTip text="budget_ledger 月行 used_cny 合计——预算口径的实际入账，与主体页月用量同源" /></th>
                <th className="px-4 py-3">域成本分布<InfoTip text="按 agent_domain 聚合的当月成本 top3 + 其他——产品域成本可见性（任务 9-B），是评估「按域设硬限」的决策依据；'(unattributed)' 表示该月无域归因的调用量" /></th>
              </tr>
            </thead>
            <tbody>
              {(monthlyRecon.data?.months ?? []).map((m) => {
                const usage = monthlyRecon.data?.usage_by_month[m]
                const shapes = monthlyRecon.data?.reservations_by_month[m]?.by_shape ?? []
                const exactCount = shapes.filter((s) => s.status === 'settled' && s.settle_kind === '').reduce((acc, s) => acc + s.count, 0)
                const estimatedCount = shapes.filter((s) => s.settle_kind === 'estimated').reduce((acc, s) => acc + s.count, 0)
                const resolvedCount = shapes.filter((s) => s.resolution).reduce((acc, s) => acc + s.count, 0)
                const ledgerUsed = Object.values(monthlyRecon.data?.ledger_by_month[m]?.by_scope ?? {}).reduce(
                  (acc, slot) => acc + Number(slot.used_cny), 0,
                )
                // 域成本 top3（按金额降序），其余合并为「其他」
                const domainEntries = Object.entries(usage?.by_domain ?? {})
                  .sort(([, a], [, b]) => Number(b.cost_cny) - Number(a.cost_cny))
                const topDomains = domainEntries.slice(0, 3)
                const restCost = domainEntries.slice(3).reduce((acc, [, slot]) => acc + Number(slot.cost_cny), 0)
                return (
                  <tr key={m} className="border-b border-slate-50">
                    <td className="px-4 py-3 font-mono text-[11px]">{m}</td>
                    <td className="px-4 py-3 text-right font-mono">{formatCny(usage?.total_cny)}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{usage?.calls ?? 0}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{exactCount}</td>
                    <td className="px-4 py-3 text-right font-mono text-amber-700">{estimatedCount}</td>
                    <td className="px-4 py-3 text-right font-mono text-text-secondary">{resolvedCount}</td>
                    <td className="px-4 py-3 text-right font-mono">{formatCny(ledgerUsed.toFixed(6))}</td>
                    <td className="px-4 py-3 text-[10px] leading-4 text-text-secondary">
                      {domainEntries.length === 0
                        ? <span className="text-text-muted">—</span>
                        : topDomains.map(([domain, slot]) => (
                          <span key={domain} className="mr-2 whitespace-nowrap" title={domain}>
                            <span className="font-mono">{domain}</span> {formatCny(slot.cost_cny)}
                          </span>
                        ))}
                      {restCost > 0 && <span className="mr-2 whitespace-nowrap text-text-muted">其他 {formatCny(restCost.toFixed(6))}</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {monthlyRecon.data && (monthlyRecon.data.months ?? []).length === 0 && (
            <EmptyRow text="统计窗口内暂无月度数据" />
          )}
          {monthlyRecon.data?.note && (
            <p className="px-4 py-2 text-[10px] text-text-muted">{monthlyRecon.data.note}</p>
          )}
        </div>
      </section>
    </div>
  )
}
