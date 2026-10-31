'use client'

// cs-workstation-v2 P5：退款审核工作台（/cs2/refunds，supervisor 专属——
// nav 已按角色隐藏；后端双重闸）。队列筛选 + 详情（原因/金额/政策版本）+
// 意见必填 + 通过/驳回；终态锁定显示审核记录。
import { useCallback, useEffect, useState } from 'react'
import {
  listRefundQueue,
  reviewRefund,
  type RefundQueueItem,
} from '@/cs2/workspace/api'

const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'pending_review', label: '待审核' },
  { key: 'approved', label: '已通过' },
  { key: 'rejected', label: '已驳回' },
]

const STATUS_ZH: Record<string, string> = {
  pending_review: '待审核',
  reviewing: '审核中',
  approved: '已通过',
  rejected: '已驳回',
  cancelled: '已撤销',
  resolved: '已完成',
}

function statusBadgeClass(status: string): string {
  if (status === 'approved' || status === 'resolved') {
    return 'bg-emerald-50 text-emerald-600'
  }
  if (status === 'rejected') return 'bg-red-50 text-red-600'
  if (status === 'pending_review' || status === 'reviewing') {
    return 'bg-amber-50 text-amber-600'
  }
  return 'bg-slate-100 text-slate-500'
}

function fmtAmount(amount: string | number | null | undefined): string {
  if (amount == null || amount === '') return '—'
  const n = typeof amount === 'number' ? amount : Number(amount)
  return Number.isFinite(n) ? `¥${n.toFixed(2)}` : String(amount)
}

export default function Cs2RefundsPage() {
  const [status, setStatus] = useState('')
  const [items, setItems] = useState<RefundQueueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [opinion, setOpinion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(async (statusFilter: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await listRefundQueue(statusFilter || undefined)
      setItems(data.items ?? [])
    } catch {
      setError('审核队列加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload(status)
  }, [reload, status])

  const selected = items.find((item) => item.request_id === selectedId) ?? null
  const final = selected
    ? ['approved', 'rejected', 'cancelled', 'resolved'].includes(selected.status)
    : false

  const submit = useCallback(
    async (decision: 'approved' | 'rejected') => {
      if (!selected || !opinion.trim() || submitting) return
      setSubmitting(true)
      setNotice(null)
      try {
        const result = await reviewRefund(selected.request_id, decision, opinion.trim())
        setNotice(
          `已${decision === 'approved' ? '通过' : '驳回'} ${result.request_id.slice(0, 8)}…`
            + (result.status_display ? `（${result.status_display}）` : ''),
        )
        setOpinion('')
        setSelectedId(null)
        await reload(status)
      } catch {
        setNotice('审核提交失败：可能已被审核或状态变化，请刷新后重试')
        await reload(status)
      } finally {
        setSubmitting(false)
      }
    },
    [opinion, reload, selected, status, submitting],
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <h1 className="text-sm font-semibold text-slate-900">退款审核</h1>
        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">
          supervisor
        </span>
        <div className="ml-4 flex gap-1">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={
                status === tab.key
                  ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
                  : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
              }
              onClick={() => setStatus(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {notice && (
          <span className="ml-auto text-[11px] text-slate-500">{notice}</span>
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 w-[46%] overflow-auto border-r border-slate-200 bg-white p-2">
          {loading && <p className="p-3 text-xs text-slate-400">加载中…</p>}
          {error && <p className="p-3 text-xs text-red-500">{error}</p>}
          {!loading && !error && items.length === 0 && (
            <p className="p-3 text-xs text-slate-400">暂无退款申请</p>
          )}
          {items.map((item) => (
            <button
              key={item.request_id}
              type="button"
              className={`mb-1.5 w-full rounded-lg border p-2.5 text-left ${
                selectedId === item.request_id
                  ? 'border-indigo-300 bg-indigo-50/60'
                  : 'border-slate-100 hover:bg-slate-50'
              }`}
              onClick={() => {
                setSelectedId(item.request_id)
                setOpinion('')
                setNotice(null)
              }}
            >
              <span className="flex items-center gap-2">
                <b className="text-xs text-slate-900">
                  {item.request_no || item.request_id.slice(0, 8)}
                </b>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] ${statusBadgeClass(item.status)}`}
                >
                  {STATUS_ZH[item.status] ?? item.status}
                </span>
                <span className="ml-auto text-[11px] text-slate-500">
                  {fmtAmount(item.amount)}
                </span>
              </span>
              <span className="mt-1 block truncate text-[11px] text-slate-500">
                {item.order_no ? `订单 ${item.order_no}` : ''}
                {item.reason ? ` · ${item.reason}` : ''}
              </span>
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {!selected ? (
            <p className="text-xs text-slate-400">← 选择左侧退款申请查看详情并审核</p>
          ) : (
            <div className="max-w-xl space-y-3">
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="flex items-center gap-2">
                  <b className="text-sm text-slate-900">
                    {selected.request_no || selected.request_id.slice(0, 8)}
                  </b>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[11px] ${statusBadgeClass(selected.status)}`}
                  >
                    {STATUS_ZH[selected.status] ?? selected.status}
                  </span>
                </p>
                <dl className="mt-2 space-y-1 text-xs text-slate-600">
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">申请金额</dt>
                    <dd>{fmtAmount(selected.amount)}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">关联订单</dt>
                    <dd>{selected.order_no || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">申请原因</dt>
                    <dd>{selected.reason || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">政策版本</dt>
                    <dd>{selected.policy_version || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">申请用户</dt>
                    <dd>{selected.user_id || '—'}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-20 text-slate-400">申请时间</dt>
                    <dd>{selected.created_at ?? '—'}</dd>
                  </div>
                </dl>
                <p className="mt-2 rounded-lg bg-amber-50 p-2 text-[11px] text-amber-700">
                  退款编号不代表已退款；审核通过后由订单系统退回用户钱包。
                </p>
              </div>

              {final ? (
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                  <p className="font-medium">审核记录（终态不可回退）</p>
                  {selected.review ? (
                    <p className="mt-1">
                      {selected.review.decision === 'approved' ? '通过' : '驳回'}
                      {" · "}审核人 {selected.review.reviewer_id}
                      {" · "}意见：{selected.review.opinion}
                    </p>
                  ) : (
                    <p className="mt-1">该申请已处于终态（无审核记录详情）</p>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-200 bg-white p-3">
                  <label
                    htmlFor="refund-opinion"
                    className="block text-xs font-medium text-slate-700"
                  >
                    审核意见（必填）
                  </label>
                  <textarea
                    id="refund-opinion"
                    aria-label="审核意见"
                    className="mt-1.5 min-h-16 w-full resize-none rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs outline-none focus:border-indigo-500"
                    placeholder="请填写审核意见，例如：凭证齐全，同意退款 / 超过政策窗口"
                    value={opinion}
                    onChange={(e) => setOpinion(e.target.value)}
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-40"
                      disabled={!opinion.trim() || submitting}
                      onClick={() => void submit('approved')}
                    >
                      {submitting ? '提交中…' : '通过并退款'}
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-40"
                      disabled={!opinion.trim() || submitting}
                      onClick={() => void submit('rejected')}
                    >
                      驳回
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
