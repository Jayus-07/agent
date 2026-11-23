'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { getCachedUser, logout } from '@/lib/auth'
import { CS2_NAV_ITEMS } from './nav'
import { getMyStatus, type AgentStatusView } from './workspace/api'

// cs-workstation-v2 P0 新工作台壳：独立于旧 components/layout/Sidebar，
// Phase 8 切换 /cs/workspace 后旧壳整体退役。
// P5：nav 按 cs_role 过滤 supervisorOnly 项（退款审核/看板对 agent 隐藏）；
// 角色未加载前不显示受限入口（安全默认）。
export default function WorkstationShell({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const [role, setRole] = useState<string | null>(null)
  const [username, setUsername] = useState<string>('')
  const [loggingOut, setLoggingOut] = useState(false)

  useEffect(() => {
    let cancelled = false
    const cached = getCachedUser()
    if (cached?.username) {
      setUsername(cached.username)
    }
    getMyStatus()
      .then((status: AgentStatusView) => {
        if (!cancelled) {
          setRole(String(status.cs_role ?? 'agent'))
          if (status.display_name) setUsername(status.display_name)
        }
      })
      .catch(() => {
        if (!cancelled) setRole('agent')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const isSupervisor = role === 'supervisor' || role === 'admin'
  const roleLabel = isSupervisor ? '客服主管' : '客服坐席'
  const displayUser = username || 'ops_cs_agent'

  const handleLogout = async () => {
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await logout()
    } finally {
      window.location.assign('/login')
    }
  }

  return (
    <div className="flex h-screen w-full bg-slate-50">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="text-sm font-semibold text-slate-900">客服工作台</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
            v2
          </span>
        </div>
        <nav className="flex-1 space-y-1 px-2" aria-label="坐席工作台导航">
          {CS2_NAV_ITEMS.map((item) => {
            if (item.supervisorOnly && !isSupervisor) return null
            const active =
              pathname === item.href || pathname.startsWith(`${item.href}/`)
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`block rounded-md px-3 py-2 text-sm ${
                  active
                    ? 'bg-slate-900 font-medium text-white'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>
        <div className="px-4 py-3 text-xs text-slate-400">
          Phase 8 切换 /cs/workspace
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center justify-between border-b border-slate-200 bg-white px-4">
          <div className="text-xs text-slate-500" />
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-xs text-slate-700">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span className="font-medium">{roleLabel}</span>
              <span className="text-slate-400">·</span>
              <span className="text-slate-600">{displayUser}</span>
            </span>
            <button
              type="button"
              onClick={handleLogout}
              disabled={loggingOut}
              className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-50 hover:text-red-600 disabled:opacity-50"
              data-testid="logout-button"
            >
              {loggingOut ? '正在退出…' : '退出登录'}
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
