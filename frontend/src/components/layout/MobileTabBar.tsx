'use client'

/**
 * MobileTabBar — workspace 移动断点底部导航（UX P2-⑨，≤768px）
 *
 * 设计文档 §4.1：仅 (workspace) 做 ≤768px 断点——侧栏收纳为底部四 tab
 * （对话/任务/报告/我的），移动端可完成「问答 → 报告」全旅程。
 * (admin) 在 frontend-admin，桌面优先，不在本组件职责内。
 *
 * 断点用 Tailwind md=768px：本组件 md:hidden，桌面由 Sidebar 接管。
 */
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Bell, FileText, ListChecks, MessageCircle } from 'lucide-react'
import type { ReactNode } from 'react'

export interface TabItem {
  label: string
  path: string
  icon: ReactNode
  /** 高亮判定：exact 严格匹配；prefix 按前缀匹配 */
  match: 'exact' | 'prefix'
}

/** 纯函数：当前路由是否命中 tab（导出供测试，无 DOM 依赖） */
export function isTabActive(pathname: string, tab: Pick<TabItem, 'path' | 'match'>): boolean {
  if (tab.match === 'exact') return pathname === tab.path
  return pathname === tab.path || pathname.startsWith(`${tab.path}/`)
}

export const MOBILE_TABS: TabItem[] = [
  { label: '对话', path: '/agent', match: 'exact', icon: <MessageCircle size={20} /> },
  { label: '任务', path: '/agent/tasks', match: 'prefix', icon: <ListChecks size={20} /> },
  { label: '报告', path: '/reports', match: 'prefix', icon: <FileText size={20} /> },
  { label: '我的告警', path: '/alerts', match: 'prefix', icon: <Bell size={20} /> },
]

export default function MobileTabBar() {
  const pathname = usePathname()

  return (
    <nav
      aria-label="移动端主导航"
      className="fixed bottom-0 inset-x-0 z-40 flex md:hidden glass border-t border-black/5 h-16"
    >
      {MOBILE_TABS.map((tab) => {
        const active = isTabActive(pathname, tab)
        return (
          <Link
            key={tab.path}
            href={tab.path}
            aria-current={active ? 'page' : undefined}
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 text-[11px] transition-colors ${
              active ? 'text-accent' : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            {tab.icon}
            <span>{tab.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
