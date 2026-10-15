'use client'

import { useEffect, useState } from 'react'
import { CheckCircle, Pencil, XCircle } from 'lucide-react'
import type { PendingActionSnapshot } from '@/api/cs'

interface Props {
  pending: PendingActionSnapshot
  busy?: boolean
  disabled?: boolean
  onConfirm: () => void
  onCancel: () => void
  onUpdateReason?: (reason: string) => Promise<void>
}

const ACTION_LABELS: Record<string, string> = {
  refund: '申请退款',
  refund_request: '提交退款申请',
  return: '申请退货',
  exchange: '申请换货',
  cancel_order: '取消订单',
}

export default function CSConfirmCard({
  pending, busy = false, disabled = false, onConfirm, onCancel, onUpdateReason,
}: Props) {
  const [editingReason, setEditingReason] = useState(false)
  const [reason, setReason] = useState(pending.reason || '')
  const [savingReason, setSavingReason] = useState(false)
  useEffect(() => {
    setReason(pending.reason || '')
    setEditingReason(false)
  }, [pending.proposal_id, pending.version, pending.reason])

  const actionable = pending.state === 'pending' && !busy && !disabled
  const canEditReason = pending.action_type === 'refund_request' && !!onUpdateReason
  const stateText = pending.state === 'paused_handoff'
    ? '人工客服接管中，确认已暂停'
    : pending.state === 'expired'
      ? '此确认已过期，请重新发起操作'
      : pending.expires_at
        ? `有效期至 ${new Date(pending.expires_at).toLocaleString()}`
        : '请检查信息后确认'

  return (
    <div className="flex gap-3 mb-4 px-3 sm:px-4" data-testid="cs-confirm-card">
      <div className="flex-1 min-w-0 sm:ml-11">
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 sm:px-4 py-3">
          <p className="text-xs font-medium text-amber-700 mb-2">请确认以下操作</p>
          <p className="text-sm font-medium text-amber-950 break-words">
            {ACTION_LABELS[pending.action_type] || '待确认操作'}
          </p>
          <p className="text-sm text-amber-900 mt-1 mb-2 break-words">{pending.summary}</p>
          <p className="text-xs text-amber-800/80 mb-3 break-words">
            操作对象：{pending.masked_target} · {stateText}
          </p>
          {canEditReason && editingReason ? (
            <div className="space-y-2">
              <label className="block text-xs font-medium text-amber-900" htmlFor="cs-refund-reason">
                退款原因（最多 500 字）
              </label>
              <textarea
                id="cs-refund-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                maxLength={500}
                rows={3}
                disabled={!actionable || savingReason}
                className="w-full rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm text-text-primary
                  focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-60"
              />
              <p className="text-xs text-amber-800">订单与金额不会改变；保存后需要重新确认。</p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={!actionable || savingReason || !reason.trim()}
                  onClick={async () => {
                    if (!onUpdateReason) return
                    setSavingReason(true)
                    try {
                      await onUpdateReason(reason.trim())
                      setEditingReason(false)
                    } catch {
                      // 错误由父组件展示；保持输入内容供用户修正或重试。
                    } finally {
                      setSavingReason(false)
                    }
                  }}
                  className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white
                    hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {savingReason ? '保存中…' : '保存原因'}
                </button>
                <button
                  type="button"
                  disabled={savingReason}
                  onClick={() => { setReason(pending.reason || ''); setEditingReason(false) }}
                  className="px-3 py-1.5 text-xs rounded-lg bg-white text-text-secondary
                    border border-border-subtle hover:bg-gray-50 disabled:opacity-50"
                >
                  返回确认
                </button>
              </div>
            </div>
          ) : (
          <div className="flex flex-col min-[360px]:flex-row flex-wrap gap-2">
            {canEditReason && (
              <button
                type="button"
                onClick={() => setEditingReason(true)}
                disabled={!actionable}
                className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-white
                  text-text-secondary border border-border-subtle hover:bg-gray-50
                  disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Pencil size={12} /> 修改原因
              </button>
            )}
            <button
              onClick={onConfirm}
              disabled={!actionable}
              aria-busy={busy}
              data-testid="cs-confirm-submit"
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg
                bg-green-600 text-white hover:bg-green-700 transition-colors
                disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CheckCircle size={12} />
              {busy ? '处理中…' : '确认'}
            </button>
            <button
              onClick={onCancel}
              disabled={!actionable}
              data-testid="cs-confirm-cancel"
              className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg
                bg-white text-text-secondary border border-border-subtle
                hover:bg-gray-50 transition-colors disabled:cursor-not-allowed
                disabled:opacity-50"
            >
              <XCircle size={12} />
              取消
            </button>
          </div>
          )}
        </div>
      </div>
    </div>
  )
}
