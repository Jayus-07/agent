'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
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

  useEffect(() => {
    let cancelled = false
    getMyStatus()
      .then((status: AgentStatusView) => {
        if (!cancelled) setRole(String(status.cs_role ?? 'agent'))
      })
      .catch(() => {
        if (!cancelled) setRole('agent')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const isSupervisor = role === 'supervisor' || role === 'admin'

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
        <header className="flex h-12 items-center justify-end border-b border-slate-200 bg-white px-4">
          {/* P3 落位：坐席状态 / 接单开关 / 通知中心 / 视角切换 */}
          <span className="text-xs text-slate-400">
            坐席状态 · 接单开关 · 通知中心（P3）
          </span>
        </header>
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
