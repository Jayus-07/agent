'use client'

// cs-workstation-v2 P2：确认卡（退款/取消两类）——消费权威待确认快照，
// 有效期倒计时（过期置灰）、修改退款原因（版本 CAS）、确认/取消幂等提交。
import { useCallback, useEffect, useState } from 'react'
import {
  confirmAction,
  fetchPendingSnapshot,
  updateRefundReason,
  type PendingSnapshot,
} from './cs2Api'

const ACTION_LABELS: Record<string, string> = {
  refund_request: '提交退款申请',
  order_cancel: '取消订单',
}

function leftSeconds(expiresAt: string | null, now: number): number | null {
  if (!expiresAt) return null
  const ts = Date.parse(expiresAt)
  if (!Number.isFinite(ts)) return null
  return Math.max(0, Math.floor((ts - now) / 1000))
}

function fmt(s: number): string {
  const m = Math.floor(s / 60)
  const ss = s % 60
  return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

export default function ConfirmCard({
  conversationId,
  onReceipt,
}: {
  conversationId: string
  /** 确认/取消后向会话追加回执气泡 */
  onReceipt: (text: string) => void
}) {
  const [snapshot, setSnapshot] = useState<PendingSnapshot | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const [editing, setEditing] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    fetchPendingSnapshot(conversationId)
      .then((snap) => {
        setSnapshot(snap)
        setError(null)
      })
      .catch(() => setSnapshot(null))
      .finally(() => setLoaded(true))
  }, [conversationId])

  useEffect(() => {
    setLoaded(false)
    setSnapshot(null)
    setEditing(false)
    reload()
  }, [reload])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  if (!loaded || !snapshot || snapshot.state === 'paused_handoff') return null
  if (snapshot.state === 'expired') {
    return (
      <div className="mx-auto w-full max-w-md rounded-xl border border-slate-200 bg-white p-3 text-xs">
        <div className="mb-1.5 flex items-center gap-2">
          <b className="text-slate-900">确认操作</b>
          <span className="rounded-full bg-red-50 px-2 py-0.5 text-[11px] text-red-500">已过期</span>
        </div>
        <p className="text-slate-500">此确认已过期，请重新发起。</p>
      </div>
    )
  }

  const secondsLeft = leftSeconds(snapshot.expires_at, now)
  const isRefund = snapshot.action_type === 'refund_request'
  const label = ACTION_LABELS[snapshot.action_type] ?? '业务操作'

  const submit = async (decision: 'confirm' | 'cancel') => {
    setSubmitting(true)
    setError(null)
    try {
      const outcome = await confirmAction(
        conversationId, decision, snapshot.proposal_id, snapshot.version,
      )
      const zh: Record<string, string> = {
        success: '✅ 操作成功',
        failed: '❌ 操作执行失败',
        cancelled: '已取消本次操作',
        expired: '此确认已过期，请重新发起',
        duplicate: '该确认已被处理，请刷新会话状态',
      }
      onReceipt(`${zh[outcome.status] ?? outcome.status}：${outcome.answer ?? ''}`)
      setSnapshot(null)
    } catch {
      setError('确认请求失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const saveReason = async () => {
    if (!reason.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await updateRefundReason(
        conversationId, snapshot.proposal_id, snapshot.version, reason.trim(),
      )
      setEditing(false)
      reload()
    } catch {
      setError('原因更新失败：申请状态可能已变化，请刷新后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-md rounded-xl border border-slate-200 bg-white p-3 text-xs">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <b className="text-slate-900">确认操作</b>
        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">{label}</span>
        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-600">
          待确认 · v{snapshot.version}
        </span>
        {secondsLeft != null && (
          <span className={`ml-auto text-[11px] ${secondsLeft <= 60 ? 'text-red-500' : 'text-amber-600'}`}>
            ⏳ 有效期 {fmt(secondsLeft)}
          </span>
        )}
      </div>
      <Kv k="内容" v={snapshot.summary} />
      <Kv k="对象（脱敏）" v={snapshot.masked_target} />
      {snapshot.reason && <Kv k="原因" v={snapshot.reason} />}
      {error && <p className="mt-1.5 text-[11px] text-red-500">{error}</p>}
      {editing ? (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            className="min-h-12 resize-y rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-800"
            maxLength={500}
            placeholder="请输入退款原因（≤500 字）"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] text-white disabled:opacity-40"
              disabled={submitting || !reason.trim()}
              onClick={() => void saveReason()}
            >
              保存原因（版本 v{snapshot.version + 1}）
            </button>
            <button
              type="button"
              className="rounded-lg px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-50"
              onClick={() => setEditing(false)}
            >
              返回确认
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {isRefund && (
            <button
              type="button"
              className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
              onClick={() => setEditing(true)}
            >
              修改原因
            </button>
          )}
          <button
            type="button"
            className={`rounded-lg px-2.5 py-1 text-[11px] text-white disabled:opacity-40 ${
              isRefund ? 'bg-emerald-600' : 'bg-indigo-600'
            }`}
            disabled={submitting || (secondsLeft != null && secondsLeft <= 0)}
            onClick={() => void submit('confirm')}
          >
            {isRefund ? '确认退款' : '确认取消订单'}
          </button>
          <button
            type="button"
            className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            disabled={submitting}
            onClick={() => void submit('cancel')}
          >
            取消
          </button>
        </div>
      )}
      <p className="mt-2 border-t border-dashed border-slate-100 pt-1.5 text-[11px] leading-relaxed text-slate-400">
        {isRefund
          ? '确认后进入人工审核，不代表资金已退；审核通过后货款自动退回账户余额。重复确认受幂等键保护。'
          : '确认后订单立即取消，已支付货款将全额退回账户余额；重复确认受幂等键保护。'}
      </p>
    </div>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2.5 border-b border-dashed border-slate-100 py-1">
      <span className="flex-none text-slate-400">{k}</span>
      <span className="min-w-0 break-all text-right text-slate-800">{v}</span>
    </div>
  )
}
