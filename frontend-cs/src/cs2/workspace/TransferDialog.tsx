'use client'

// cs-workstation-v2 P3：坐席间转接弹窗（目标在线/状态展示、原因必填；
// 小休/离线坐席不可转——后端同口径校验 422 兜底）。
import { useEffect, useMemo, useState } from 'react'
import { request } from '@/api/client'
import { transferConversation, type TransferResult } from './api'

interface AgentRow {
  agent_id: string
  display_name: string
  role: string
  skill: string
  status: string
  accepting: boolean
  transferable: boolean
}

async function listTenantAgents(): Promise<AgentRow[]> {
  const data = await request<{ agents: AgentRow[] }>('/api/cs/agents')
  return data.agents ?? []
}

const STATUS_ZH: Record<string, string> = {
  online: '在线',
  busy: '忙碌',
  away: '小休',
  offline: '离线',
}

export default function TransferDialog({
  conversationId,
  onClose,
  onDone,
}: {
  conversationId: string
  onClose: () => void
  onDone: (result: TransferResult) => void
}) {
  const [agents, setAgents] = useState<AgentRow[]>([])
  const [target, setTarget] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    listTenantAgents()
      .then((rows) => {
        if (!cancelled) setAgents(rows)
      })
      .catch(() => {
        if (!cancelled) setError('坐席列表加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const selectable = useMemo(() => agents.filter((a) => a.transferable), [agents])

  const submit = async () => {
    if (!target || !reason.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await transferConversation(conversationId, target, reason.trim())
      onDone(result)
    } catch {
      setError('转接失败：目标可能已被认领或状态变化，请刷新重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40"
      onClick={onClose}
    >
      <div
        className="w-[420px] max-w-[calc(100%-32px)] rounded-xl bg-white p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="转接会话"
      >
        <h3 className="mb-3 text-sm text-slate-900">转接会话 {conversationId.slice(0, 10)}…</h3>
        {error && <p className="mb-2 text-xs text-red-500">{error}</p>}
        <div className="mb-3 max-h-56 overflow-auto rounded-lg border border-slate-100">
          {selectable.length === 0 && (
            <p className="p-3 text-center text-xs text-slate-400">暂无可转接的在线坐席</p>
          )}
          {selectable.map((a) => (
            <label
              key={a.agent_id}
              className={`flex cursor-pointer items-center gap-2.5 border-b border-dashed border-slate-100 px-2.5 py-2 text-xs last:border-b-0 ${
                target === a.agent_id ? 'bg-indigo-50' : 'hover:bg-slate-50'
              }`}
            >
              <input
                type="radio"
                name="transfer-target"
                checked={target === a.agent_id}
                onChange={() => setTarget(a.agent_id)}
              />
              <span className="min-w-0 flex-1">
                <b className="text-slate-900">{a.display_name}</b>
                <span className="ml-2 text-slate-400">技能 {a.skill}</span>
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10.5px] text-slate-500">
                {STATUS_ZH[a.status] ?? a.status}
              </span>
            </label>
          ))}
          {agents
            .filter((a) => !a.transferable)
            .map((a) => (
              <div key={a.agent_id} className="flex items-center gap-2.5 px-2.5 py-2 text-xs text-slate-300">
                <span className="min-w-0 flex-1">{a.display_name}</span>
                <span>{STATUS_ZH[a.status] ?? a.status} · 不可转</span>
              </div>
            ))}
        </div>
        <textarea
          className="min-h-16 w-full resize-y rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs text-slate-800"
          placeholder="转接原因（必填）"
          maxLength={200}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-40"
            disabled={!target || !reason.trim() || submitting}
            onClick={() => void submit()}
          >
            {submitting ? '转接中…' : '发起转接'}
          </button>
        </div>
      </div>
    </div>
  )
}
