'use client'

import { CheckCircle, XCircle } from 'lucide-react'
import type { PendingActionSnapshot } from '@/api/cs'

interface Props {
  pending: PendingActionSnapshot
  busy?: boolean
  disabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

const ACTION_LABELS: Record<string, string> = {
  refund: '申请退款',
  return: '申请退货',
  exchange: '申请换货',
  cancel_order: '取消订单',
}

export default function CSConfirmCard({ pending, busy = false, disabled = false, onConfirm, onCancel }: Props) {
  const actionable = pending.state === 'pending' && !busy && !disabled
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
          <div className="flex flex-col min-[360px]:flex-row gap-2">
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
        </div>
      </div>
    </div>
  )
}
