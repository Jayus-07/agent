'use client'

// cs-workstation-v2 P3：工单管理页（状态/类型筛选 + 详情处理记录 +
// 受理/转派语义简化为状态流转：processing → pending_user/resolved/closed；
// 走 /cs/tickets/admin/*（supervisor 闸，agent 视角 403 显式提示）。
import { useCallback, useEffect, useState } from 'react'
import { request } from '@/api/client'

interface Ticket {
  ticket_id: string
  type: string
  status: string
  source: string
  conversation_id: string
  user_id: string
  priority: string
  title: string
  description: string | null
  resolution: string | null
  created_at: string
  updated_at: string
}

const STATUS_ZH: Record<string, string> = {
  open: '待受理',
  processing: '处理中',
  pending_user: '待用户响应',
  resolved: '已解决',
  closed: '已关闭',
}
const STATUS_BADGE: Record<string, string> = {
  open: 'bg-amber-50 text-amber-600',
  processing: 'bg-blue-50 text-blue-600',
  pending_user: 'bg-blue-50 text-blue-600',
  resolved: 'bg-emerald-50 text-emerald-600',
  closed: 'bg-slate-100 text-slate-500',
}

export default function TicketsAdminPage() {
  const [items, setItems] = useState<Ticket[]>([])
  const [status, setStatus] = useState('')
  const [type, setType] = useState('')
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [resolution, setResolution] = useState('')
  const [loading, setLoading] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [notice, setNotice] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    setForbidden(false)
    const params = new URLSearchParams({ limit: '100' })
    if (status) params.set('status', status)
    if (type) params.set('type', type)
    request<{ items: Ticket[] }>(`/api/cs/tickets/admin/list?${params}`)
      .then((data) => setItems(data.items ?? []))
      .catch(() => {
        setItems([])
        setForbidden(true)
      })
      .finally(() => setLoading(false))
  }, [status, type])

  useEffect(() => {
    load()
  }, [load])

  const notify = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2600)
  }

  const transition = async (ticket: Ticket, next: string) => {
    try {
      const updated = await request<Ticket>(`/api/cs/tickets/admin/${ticket.ticket_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: next, resolution: resolution.trim() || undefined }),
      })
      notify(`工单已流转为「${STATUS_ZH[next] ?? next}」`)
      setResolution('')
      setItems((prev) => prev.map((t) => (t.ticket_id === ticket.ticket_id ? updated : t)))
      if (selected?.ticket_id === ticket.ticket_id) setSelected(updated)
    } catch {
      notify('流转失败：状态机不允许或无权限')
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
        <h2 className="text-sm text-slate-900">工单管理</h2>
        <select className="h-7 rounded-lg border border-slate-200 bg-white px-2 text-xs" aria-label="状态筛选" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_ZH).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select className="h-7 rounded-lg border border-slate-200 bg-white px-2 text-xs" aria-label="类型筛选" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">全部类型</option>
          <option value="complaint">投诉</option>
          <option value="handoff_timeout">转人工超时</option>
          <option value="refund">退款</option>
        </select>
        {forbidden && (
          <span className="text-[11px] text-slate-400">工单管理需要 supervisor 权限（当前账号不可见）</span>
        )}
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 w-1/2 overflow-auto bg-white">
          {loading && <p className="p-4 text-xs text-slate-400">加载中…</p>}
          {!loading && items.length === 0 && !forbidden && (
            <p className="p-4 text-xs text-slate-400">没有匹配的工单。</p>
          )}
          {items.map((t) => (
            <button
              key={t.ticket_id}
              type="button"
              className={`block w-full border-b border-dashed border-slate-100 px-4 py-2.5 text-left text-xs hover:bg-slate-50 ${
                selected?.ticket_id === t.ticket_id ? 'bg-indigo-50' : ''
              }`}
              onClick={() => setSelected(t)}
            >
              <div className="flex items-center gap-2">
                <b className="text-slate-900">{t.title || t.ticket_id}</b>
                <span className={`rounded-full px-2 py-0.5 text-[10.5px] ${STATUS_BADGE[t.status] ?? 'bg-slate-100 text-slate-500'}`}>
                  {STATUS_ZH[t.status] ?? t.status}
                </span>
                <span className="ml-auto text-[10.5px] text-slate-400">
                  {t.created_at?.slice(5, 16).replace('T', ' ')}
                </span>
              </div>
              <div className="mt-0.5 truncate text-[11px] text-slate-500">
                {t.type} · {t.priority} · 用户 {t.user_id.slice(0, 10)}
              </div>
            </button>
          ))}
        </div>
        <div className="min-h-0 w-1/2 overflow-auto bg-slate-50 p-4">
          {!selected ? (
            <p className="text-xs text-slate-400">← 点击左侧工单查看详情与流转</p>
          ) : (
            <div className="text-xs">
              <h3 className="mb-2 text-sm text-slate-900">{selected.title || selected.ticket_id}</h3>
              <div className="mb-3 rounded-lg bg-white p-3 text-slate-600">
                <div>类型 {selected.type} · 来源 {selected.source} · 优先级 {selected.priority}</div>
                <div className="mt-1">会话 {selected.conversation_id.slice(0, 12)}… · 用户 {selected.user_id.slice(0, 12)}</div>
                {selected.description && <p className="mt-1.5 text-slate-700">{selected.description}</p>}
                {selected.resolution && <p className="mt-1.5 text-emerald-600">处理记录：{selected.resolution}</p>}
                <div className="mt-1 text-[10.5px] text-slate-400">
                  创建 {selected.created_at?.slice(5, 16).replace('T', ' ')} · 更新 {selected.updated_at?.slice(5, 16).replace('T', ' ')}
                </div>
              </div>
              {selected.status !== 'closed' && (
                <div className="rounded-lg bg-white p-3">
                  <textarea
                    className="min-h-14 w-full resize-y rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs"
                    placeholder="处理意见 / 解决说明（流转为 resolved 时建议填写）"
                    value={resolution}
                    onChange={(e) => setResolution(e.target.value)}
                  />
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selected.status === 'open' && (
                      <button type="button" className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] text-white hover:opacity-90" onClick={() => void transition(selected, 'processing')}>
                        受理
                      </button>
                    )}
                    {selected.status === 'processing' && (
                      <button type="button" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50" onClick={() => void transition(selected, 'pending_user')}>
                        等待用户响应
                      </button>
                    )}
                    {['processing', 'pending_user'].includes(selected.status) && (
                      <button type="button" className="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] text-white hover:opacity-90" onClick={() => void transition(selected, 'resolved')}>
                        标记解决
                      </button>
                    )}
                    <button type="button" className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50" onClick={() => void transition(selected, 'closed')}>
                      关闭
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {notice && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-3.5 py-2 text-xs text-white">
          {notice}
        </div>
      )}
    </div>
  )
}
