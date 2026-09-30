'use client'

import { useState } from 'react'
import { usePathname } from 'next/navigation'
import Sidebar from '@/components/layout/Sidebar'
import MobileTabBar from '@/components/layout/MobileTabBar'
import AuthGate from '@/components/AuthGate'
import { ToastProvider } from '@/components/shared/Toast'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const pathname = usePathname()

  // /agent 走任务模式：全局控制台导航让位给页面自渲染的 TaskSidebar
  // （业务入口常驻上半区「全部功能」折叠组，会话历史在下半区；
  //   两处侧栏均从 navConfig 的 NAV 数组渲染，故 NAV 加项即两处同时生效）。
  // 用精确匹配而非 startsWith，避免子路径误判（2026-09-30 修正过期注释：
  // 原文提到的 /agent/tasks 页面目录已于 09-21 裁撤）。
  const isTaskMode = pathname === '/agent'
  // 登录页 / 注册页 / 统一门户主页 独立呈现：不渲染全局侧边栏与移动端 tab
  // （AuthGate 同样对这些路径放行）。用精确匹配，避免误伤 /login-xxx 之类子路径。
  const isStandalone =
    pathname === '/login' ||
    pathname === '/register' ||
    pathname === '/'

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent AI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="h-full flex bg-surface-root">
        {!isTaskMode && !isStandalone && (
          <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        )}
        <main className="flex-1 flex flex-col min-w-0 pb-16 md:pb-0">
          <AuthGate>
            <ToastProvider>{children}</ToastProvider>
          </AuthGate>
          {/* UX P2-⑨ 移动断点：≤768px 底部四 tab（桌面 md:hidden 由 Sidebar 接管）；
              登录/注册/门户等独立页不放导航 */}
          {!isStandalone && <MobileTabBar />}
        </main>
      </body>
    </html>
  )
}
