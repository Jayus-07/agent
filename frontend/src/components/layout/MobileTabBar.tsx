'use client'

/**
 * MobileTabBar — workspace 移动断点底部导航（UX P2-⑨，≤768px）
 *
 * 设计文档 §4.1：仅 (workspace) 做 ≤768px 断点——侧栏收纳为底部 tab，
 * (admin) 在 frontend-admin，桌面优先，不在本组件职责内。
 *
 * 断点用 Tailwind md=768px：本组件 md:hidden，桌面由 Sidebar 接管。
 *
 * - 2026-09-30 修正（**修复失效导航**）：原四 tab（对话/任务/报告/我的告警）
 *   中后三个指向 /agent/tasks、/reports、/alerts —— 这三个页面目录已在
 *   2026-09-21 三次收敛时删除（见 navConfig.tsx 演进记录），**移动端点进去
 *   直接 404**；而 MobileTabBar.test.ts 当时仍在断言这三个 tab 存在，即
 *   测试在保护一段失效代码。现收敛为「对话 + 旅游规划」两个**真实可达**的 tab，
 *   与 navConfig.tsx 的用户端边界（AI 对话 + 旅游规划）保持一致。
 */
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { MessageCircle, Plane } from 'lucide-react'
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
  // 2026-09-30：旅游规划（/travel 页面 2026-09-22 建成，此前移动端无法触达）
  { label: '旅游规划', path: '/travel', match: 'prefix', icon: <Plane size={20} /> },
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
