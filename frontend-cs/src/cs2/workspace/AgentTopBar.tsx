'use client'

// cs-workstation-v2 P3：工作台顶栏 —— 坐席状态切换（持久）+ 通知中心（铃铛）。
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getMyStatus,
  updateMyStatus,
  type AgentStatusView,
  type AgentWorkStatus,
} from './api'

export interface NoticeItem {
  id: string
  level: 'info' | 'warn' | 'breach'
  text: string
  conversationId?: string
  at: number
}

const STATUS_ZH: Record<string, string> = {
  online: '在线',
  busy: '忙碌',
  away: '小休',
  offline: '离线',
}

export function AgentStatusBar({ onNotice }: { onNotice: (n: NoticeItem) => void }) {
  const [view, setView] = useState<AgentStatusView | null>(null)
  const [saving, setSaving] = useState(false)

  const reload = useCallback(() => {
    getMyStatus()
      .then(setView)
      .catch(() => {})
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 30_000)
    return () => clearInterval(t)
  }, [reload])

  const change = async (status: AgentWorkStatus) => {
    setSaving(true)
    try {
      setView(await updateMyStatus(status))
    } catch {
      onNotice({ id: crypto.randomUUID(), level: 'warn', text: '状态切换失败，请重试', at: Date.now() })
    } finally {
      setSaving(false)
    }
  }

  return (
    <select
      className="h-7 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 disabled:opacity-50"
      aria-label="坐席状态"
      disabled={saving}
      value={view?.status ?? ''}
      onChange={(e) => void change(e.target.value as AgentWorkStatus)}
    >
      {!view && <option value="">…</option>}
      {(['online', 'busy', 'away', 'offline'] as const).map((s) => (
        <option key={s} value={s}>{STATUS_ZH[s]}</option>
      ))}
    </select>
  )
}

export function NotificationCenter({
  notices,
  onLocate,
}: {
  notices: NoticeItem[]
  onLocate?: (conversationId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [seen, setSeen] = useState(0)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const unread = Math.max(0, notices.length - seen)

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (open && boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false)
        setSeen(notices.length)
      }
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [open, notices.length])

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        className="relative rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
        aria-label={`通知中心（${unread} 条未读）`}
        onClick={() => {
          setOpen((v) => !v)
          if (!open) setSeen(notices.length)
        }}
      >
        🔔
        {unread > 0 && (
          <span className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-red-500 px-1 text-[10px] text-white">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-40 w-80 rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
          {notices.length === 0 ? (
            <p className="p-3 text-center text-xs text-slate-400">暂无通知（SLA 告警 / 转接请求 / 新 offer 会出现在这里）</p>
          ) : (
            notices.map((n) => (
              <button
                key={n.id}
                type="button"
                className={`mb-1 block w-full rounded-lg border px-2.5 py-2 text-left text-xs ${
                  n.level === 'breach'
                    ? 'border-red-200 bg-red-50 text-red-600'
                    : n.level === 'warn'
                      ? 'border-amber-200 bg-amber-50 text-amber-600'
                      : 'border-slate-100 bg-slate-50 text-slate-600'
                }`}
                onClick={() => {
                  if (n.conversationId && onLocate) onLocate(n.conversationId)
                  setOpen(false)
                }}
              >
                {n.text}
                <span className="ml-1.5 text-[10px] opacity-60">
                  {new Date(n.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
