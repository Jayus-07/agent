'use client'

// cs-workstation-v2 P3：会话管理页（关键词/状态筛选 + 详情：消息回放 +
// Trace 入口 + 主管重派/强制接管/关闭）。重派复用 /cs/handoffs/{id}/reassign。
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { request } from '@/api/client'

interface ConvSummary {
  conversation_id: string
  user_id: string
  conversation_status: string
  handling_mode: string
  priority: string
  summary: string | null
  last_activity_at: string
  created_at: string
}

interface ConvDetail extends ConvSummary {
  context_summary: string | null
  trace_count: number
  last_trace_id: string | null
  messages: Array<{
    message_id: string
    sender_type: string
    content: string
    created_at: string
  }>
}

const STATE_ZH: Record<string, string> = {
  open: '进行中',
  pending: '挂起',
  resolved: '已解决',
  snoozed: '暂缓',
}

export default function ConversationsAdminPage() {
  const [items, setItems] = useState<ConvSummary[]>([])
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [selected, setSelected] = useState<ConvDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState('')
  const [handoffId, setHandoffId] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams({ limit: '50' })
    if (q) params.set('q', q)
    if (status) params.set('status', status)
    request<{ items: ConvSummary[] }>(`/api/cs/conversations?${params}`)
      .then((data) => setItems(data.items ?? []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [q, status])

  useEffect(() => {
    load()
  }, [load])

  const openDetail = (conversationId: string) => {
    request<ConvDetail>(`/api/cs/conversations/${conversationId}`)
      .then(setSelected)
      .catch(() => setSelected(null))
    // 顺带查活跃 handoff（主管操作定位）
    request<{ items: Array<{ handoff_id: string; conversation_id: string; handoff_state: string }> }>(
      '/api/cs/conversations/handoff/queue',
    )
      .then((data) => {
        const hit = (data.items ?? []).find((i) => i.conversation_id === conversationId)
        setHandoffId(hit?.handoff_id ?? '')
      })
      .catch(() => setHandoffId(''))
  }

  const notify = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2600)
  }

  const closeConv = async () => {
    if (!selected) return
    try {
      await request(`/api/cs/conversations/${selected.conversation_id}/close`, {
        method: 'POST',
        body: JSON.stringify({ agent_id: '' }),
      })
      notify('会话已关闭')
      openDetail(selected.conversation_id)
      load()
    } catch {
      notify('关闭失败（可能需当前认领坐席操作）')
    }
  }

  const forceClaim = async () => {
    if (!selected) return
    try {
      await request(`/api/cs/conversations/${selected.conversation_id}/claim`, {
        method: 'POST',
        body: JSON.stringify({ agent_id: '' }),
      })
      notify('已接管（认领）该会话')
      openDetail(selected.conversation_id)
    } catch {
      notify('接管失败：可能已被他人认领或状态不允许')
    }
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-h-0 w-1/2 flex-col border-r border-slate-200">
        <div className="flex flex-none flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
          <h2 className="text-sm text-slate-900">会话管理</h2>
          <input
            className="h-7 min-w-40 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-xs"
            placeholder="搜索会话号 / 关键词"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select
            className="h-7 rounded-lg border border-slate-200 bg-white px-2 text-xs"
            aria-label="状态筛选"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="open">进行中</option>
            <option value="pending">挂起</option>
            <option value="resolved">已解决</option>
          </select>
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-white">
          {loading && <p className="p-4 text-xs text-slate-400">加载中…</p>}
          {!loading && items.length === 0 && (
            <p className="p-4 text-xs text-slate-400">没有匹配的会话。</p>
          )}
          {items.map((c) => (
            <button
              key={c.conversation_id}
              type="button"
              className={`mb-0.5 block w-full px-4 py-2.5 text-left text-xs hover:bg-slate-50 ${
                selected?.conversation_id === c.conversation_id ? 'bg-indigo-50' : ''
              }`}
              onClick={() => openDetail(c.conversation_id)}
            >
              <div className="flex items-center gap-2">
                <b className="text-slate-900">{c.summary || `会话 ${c.conversation_id.slice(0, 10)}`}</b>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10.5px] text-slate-500">
                  {STATE_ZH[c.conversation_status] ?? c.conversation_status}
                </span>
                <span className="ml-auto text-[10.5px] text-slate-400">
                  {c.last_activity_at?.slice(5, 16).replace('T', ' ')}
                </span>
              </div>
              <div className="mt-0.5 truncate text-[11px] text-slate-500">
                用户 {c.user_id.slice(0, 12)} · {c.handling_mode}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 w-1/2 overflow-auto bg-slate-50">
        {!selected ? (
          <p className="p-6 text-xs text-slate-400">← 点击左侧会话查看详情（消息回放 / Trace / 主管操作）</p>
        ) : (
          <div className="p-4">
            <h3 className="mb-2 text-sm text-slate-900">
              {selected.summary || `会话 ${selected.conversation_id.slice(0, 10)}`}
            </h3>
            <div className="mb-3 flex flex-wrap gap-2">
              <button type="button" className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50" onClick={() => void forceClaim()}>
                强制接管
              </button>
              <button
                type="button"
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                disabled={!handoffId}
                title={handoffId ? undefined : '无进行中人工工单（仅排队/派单中可重派）'}
                onClick={() => {
                  if (!handoffId) return
                  request(`/api/cs/handoffs/${handoffId}/reassign`, {
                    method: 'POST',
                    body: JSON.stringify({ reason: 'supervisor 重新派单' }),
                  })
                    .then(() => notify('已重派（回到派单队列）'))
                    .catch(() => notify('重派失败'))
                }}
              >
                重派
              </button>
              <button type="button" className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50" onClick={() => void closeConv()}>
                关闭会话
              </button>
            </div>
            <div className="mb-3 rounded-lg bg-white p-3 text-xs text-slate-600">
              <div>处理模式：{selected.handling_mode} · 优先级 {selected.priority}</div>
              <div className="mt-1">AI 阶段摘要：{selected.context_summary || '—'}</div>
              {selected.last_trace_id && (
                <Link
                  href={`/observability/traces/${selected.last_trace_id}`}
                  className="mt-1.5 inline-block rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  打开最近 Trace（{selected.trace_count} 条）
                </Link>
              )}
            </div>
            <div className="flex flex-col gap-2">
              {(selected.messages ?? []).slice(-30).map((m) => (
                <div
                  key={m.message_id}
                  className={`max-w-[85%] rounded-xl px-3 py-2 text-xs ${
                    m.sender_type === 'user'
                      ? 'self-start bg-white'
                      : m.sender_type === 'human_agent'
                        ? 'self-end bg-indigo-50'
                        : m.sender_type === 'system'
                          ? 'self-center border border-dashed border-slate-200 text-slate-500'
                          : 'self-start bg-slate-100'
                  }`}
                >
                  <div className="mb-0.5 text-[10px] text-slate-400">
                    {m.sender_type === 'user' ? '用户' : m.sender_type === 'human_agent' ? '坐席' : m.sender_type === 'system' ? '系统' : 'AI'}
                    {' · '}
                    {m.created_at?.slice(5, 16).replace('T', ' ')}
                  </div>
                  {m.content}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {notice && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-3.5 py-2 text-xs text-white">
          {notice}
        </div>
      )}
    </div>
  )
}
