'use client'

/**
 * 待对账面板（2026-11-10 Tab 化）：队列摘要 + 展开明细（筛选/勾选批量核销/
 * 存量分类 dry-run）。逻辑自原单页整块迁移，行为不变。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { useToast } from '@/components/shared/Toast'
import {
  formatCny,
  getBudgetBacklogDryRun,
  getBudgetReconciliation,
  resolveBudgetReconciliationBatch,
  type BudgetReconciliationItem,
} from '@/api/budgets'
import { EmptyRow, InfoTip, RefreshButton, SectionTitle } from '../shared'

export default function BudgetReconciliationPanel() {
  const toast = useToast()
  const queryClient = useQueryClient()
  // 第二批：待对账闭环 UI（明细默认折叠 + 时间/口径筛选 + 勾选批量核销）
  const [reviewExpanded, setReviewExpanded] = useState(false)
  const [reviewRange, setReviewRange] = useState<'7d' | 'month' | 'all'>('7d')
  const [includeClosed, setIncludeClosed] = useState(false)
  const [includeTest, setIncludeTest] = useState(false)
  const [selectedReviews, setSelectedReviews] = useState<Set<string>>(new Set())
  const [batchAction, setBatchAction] = useState<'released' | 'written_off' | 'archived' | 'settled_actual'>('released')
  const [batchReason, setBatchReason] = useState('')
  const [batchAmount, setBatchAmount] = useState('')
  const [dryRun, setDryRun] = useState<Awaited<ReturnType<typeof getBudgetBacklogDryRun>> | null>(null)
  const [resolving, setResolving] = useState(false)

  const reconciliation = useQuery({
    queryKey: ['budget-reconciliation', includeClosed, includeTest],
    queryFn: () => getBudgetReconciliation({ includeClosed, includeTest }),
    refetchInterval: 60_000,
  })

  const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime()
  function inReviewRange(item: BudgetReconciliationItem) {
    if (reviewRange === 'all') return true
    const created = new Date(item.created_at).getTime()
    return reviewRange === '7d' ? created >= Date.now() - 7 * 86_400_000 : created >= monthStart
  }
  const reviewItems = (reconciliation.data?.items ?? []).filter(inReviewRange)
  const monthNewCount = (reconciliation.data?.items ?? []).filter((item) => new Date(item.created_at).getTime() >= monthStart).length

  function toggleReview(id: string) {
    setSelectedReviews((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }
  function toggleSelectAll() {
    const ids = reviewItems.map((item) => item.reservation_id)
    const allSelected = ids.length > 0 && ids.every((id) => selectedReviews.has(id))
    setSelectedReviews(allSelected ? new Set() : new Set(ids))
  }

  async function submitResolve() {
    if (selectedReviews.size === 0) {
      toast.error('先勾选要核销的预占单')
      return
    }
    if (!batchReason.trim()) {
      toast.error('核销必须填写原因（写入审计）')
      return
    }
    if (batchAction === 'settled_actual' && !batchAmount.trim()) {
      toast.error('settled_actual 必须填写供应商实际账单金额')
      return
    }
    if (!window.confirm(`确认对 ${selectedReviews.size} 笔预占执行 ${batchAction}？核销写审计并释放占额，不能自动回滚。`)) return
    setResolving(true)
    try {
      const result = await resolveBudgetReconciliationBatch({
        reservationIds: [...selectedReviews],
        action: batchAction,
        reason: batchReason.trim(),
        actualCny: batchAction === 'settled_actual' ? batchAmount.trim() : undefined,
      })
      toast.success(`核销完成：成功 ${result.resolved} 笔 · 跳过（已核销）${result.already_resolved} 笔`)
      setSelectedReviews(new Set())
      setBatchReason('')
      setBatchAmount('')
      setDryRun(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['budget-reconciliation'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-summary'] }),
        queryClient.invalidateQueries({ queryKey: ['budget-monthly-recon'] }),
      ])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '核销失败')
    } finally {
      setResolving(false)
    }
  }

  async function loadDryRun() {
    try {
      setDryRun(await getBudgetBacklogDryRun())
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '存量分类 dry-run 失败')
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <section className="rounded-xl border border-black/5 bg-white shadow-card">
        <SectionTitle title="待对账" action={<RefreshButton onClick={() => reconciliation.refetch()} loading={reconciliation.isFetching} />} />
        <div className="border-b border-slate-50 px-4 py-2.5 text-[11px] text-text-muted">
          「预占」是每次模型调用前冻结的请求级成本上限。无法估算的调用（失败可能已计费、结算落库失败、滞留超龄）转入待对账；
          小额未知用量已按预占上限估算结算（settled_estimated，不入队列）。核销动作（第二批）：released=确认未计费释放、
          written_off=小额核销不记账、archived=超期归档、settled_actual=按实际账单入账；核销写审计并同步释放占额。
          <InfoTip text="占额总额按「预占单」去重统计：一笔预占会同时在用户与租户、日与月维度落多条账本行，去重后才能与下方明细的单据金额对上。" />
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
          {' · '}本月新增异常 {monthNewCount} 笔 · 估算结算 {reconciliation.data?.report?.usage?.estimated_calls ?? 0} 笔（占全部调用 {((reconciliation.data?.report?.derived?.estimated_share ?? 0) * 100).toFixed(2)}%）
          <span className="ml-2 text-[10px] text-text-muted">未决率持续偏高 = 结算链路或供应商链路疑似系统性异常，按下方原因分布排查</span>
        </div>
        <div className="border-t border-slate-50 px-4 py-2.5 flex flex-wrap items-center gap-3 text-xs text-text-secondary">
          <button onClick={() => setReviewExpanded(!reviewExpanded)} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px]">
            {reviewExpanded ? '收起明细' : '展开明细'}
          </button>
          {!reviewExpanded && (
            <span className="text-[10px] text-text-muted">明细默认折叠（首屏只看摘要）；展开后可按时间/口径筛选并勾选批量核销</span>
          )}
          {reviewExpanded && (
            <>
              <label className="flex items-center gap-1 text-[11px]">
                <input type="checkbox" checked={includeClosed} onChange={(event) => setIncludeClosed(event.target.checked)} /> 已核销
              </label>
              <label className="flex items-center gap-1 text-[11px]">
                <input type="checkbox" checked={includeTest} onChange={(event) => setIncludeTest(event.target.checked)} /> 测试数据
              </label>
              <select value={reviewRange} onChange={(event) => setReviewRange(event.target.value as '7d' | 'month' | 'all')} className="rounded-lg border border-black/10 bg-white px-2 py-1 text-[11px]">
                <option value="7d">最近 7 天</option>
                <option value="month">本月</option>
                <option value="all">全部</option>
              </select>
              <button onClick={loadDryRun} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px]">存量分类 dry-run</button>
            </>
          )}
        </div>
        {reviewExpanded && dryRun && (
          <div className="border-t border-slate-50 bg-slate-50/60 px-4 py-2.5 text-[11px] text-text-secondary">
            存量开态 {dryRun.open_count} 笔 · 占额 {formatCny(dryRun.open_held_cny)}：
            {Object.entries(dryRun.classes).map(([cls, info]) => (
              <span key={cls} className="mr-3">{cls} {info.count} 笔（{formatCny(info.held_cny)}）→ 建议 {info.suggested_action}</span>
            ))}
            <div className="mt-1 text-[10px] text-text-muted">{dryRun.note}</div>
          </div>
        )}
        {reviewExpanded && selectedReviews.size > 0 && (
          <div className="border-t border-slate-50 bg-amber-50/60 px-4 py-2.5 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-amber-800">已选 {selectedReviews.size} 笔</span>
            <select value={batchAction} onChange={(event) => setBatchAction(event.target.value as typeof batchAction)} className="rounded-lg border border-black/10 bg-white px-2 py-1 text-[11px]">
              <option value="released">released 确认未计费释放</option>
              <option value="written_off">written_off 小额核销（不记账）</option>
              <option value="archived">archived 超期归档</option>
              <option value="settled_actual">settled_actual 按实际账单入账</option>
            </select>
            {batchAction === 'settled_actual' && (
              <input value={batchAmount} onChange={(event) => setBatchAmount(event.target.value)} placeholder="实际账单金额 ¥" inputMode="decimal" className="w-28 rounded-lg border border-black/10 px-2 py-1 text-[11px]" />
            )}
            <input value={batchReason} onChange={(event) => setBatchReason(event.target.value)} placeholder="核销原因（写入审计，必填）" className="w-56 rounded-lg border border-black/10 px-2 py-1 text-[11px]" />
            <button disabled={resolving} onClick={submitResolve} className="rounded-lg bg-accent px-3 py-1.5 text-[11px] text-white disabled:opacity-50">
              {resolving ? '执行中…' : '执行核销'}
            </button>
          </div>
        )}
        {reviewExpanded && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-[10px] text-text-muted">
                  <th className="px-4 py-3"><input type="checkbox" aria-label="全选" checked={reviewItems.length > 0 && reviewItems.every((item) => selectedReviews.has(item.reservation_id))} onChange={toggleSelectAll} /></th>
                  <th className="px-4 py-3">预占 ID<InfoTip text="一笔调用产生一个预占单；单内可能在用户/租户、日/月维度有多条账本行" /></th>
                  <th className="px-4 py-3">request / 用户 / 租户</th>
                  <th className="px-4 py-3 text-right">占额<InfoTip text="该预占单冻结的请求级成本上限（单据金额）" /></th>
                  <th className="px-4 py-3">核销状态</th>
                  <th className="px-4 py-3">原因<InfoTip text="call_failed_possibly_billed / stream_failed_possibly_billed：调用失败但供应商可能已扣费；settle_failed：结算落库失败；stale_sweep：滞留超龄自动回收" /></th>
                  <th className="px-4 py-3">发生时间</th>
                </tr>
              </thead>
              <tbody>
                {reviewItems.map((item) => (
                  <tr key={item.reservation_id} className="border-b border-slate-50">
                    <td className="px-4 py-3"><input type="checkbox" aria-label={`选择 ${item.reservation_id.slice(0, 8)}`} checked={selectedReviews.has(item.reservation_id)} onChange={() => toggleReview(item.reservation_id)} /></td>
                    <td className="px-4 py-3 font-mono text-[10px] text-text-muted">{item.reservation_id.slice(0, 8)}</td>
                    <td className="px-4 py-3 font-mono text-[10px] text-text-secondary">{item.request_id || '—'} / {item.user_id} / {item.tenant_id}</td>
                    <td className="px-4 py-3 text-right font-mono text-amber-700">{formatCny(item.reserved_cny)}</td>
                    <td className="px-4 py-3">
                      {item.resolution
                        ? <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] text-text-muted" title={`by ${item.resolved_by}${item.actual_cny ? ` · 实际 ¥${item.actual_cny}` : ''}`}>{item.resolution}</span>
                        : <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">待处理</span>}
                    </td>
                    <td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-[10px] ${item.resolution ? 'bg-slate-100 text-text-muted' : 'bg-amber-50 text-amber-700'}`}>{item.review_reason || item.status}</span></td>
                    <td className="px-4 py-3 text-text-muted">{new Date(item.created_at).toLocaleString('zh-CN')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {reviewItems.length === 0 && <EmptyRow text={includeClosed ? '该筛选条件下没有预占单' : '没有待处理预占——所有调用都已确定结算、释放或已核销'} />}
          </div>
        )}
      </section>
    </div>
  )
}
