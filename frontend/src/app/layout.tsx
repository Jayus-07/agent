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

  // /agent 由任务页自带会话侧栏；/travel 全路由由旅游首页/行程页自带导航。
  // 两者都不渲染通用业务侧栏，避免旅游手机页面与全局底栏重复。
  const isTravelMode = pathname === '/travel' || pathname.startsWith('/travel/')
  const isTaskMode = pathname === '/agent' || isTravelMode
  // 登录页 / 注册页 / 统一门户 / 独立客服助手页不渲染全局侧边栏与移动端 tab；
  // 客服助手仍由 AuthGate 做登录保护。用精确匹配，避免误伤相似子路径。
  const isStandalone =
    pathname === '/login' ||
    pathname === '/register' ||
    pathname === '/' ||
    pathname === '/customer-service'
  // 三种助手都从门户进入；/agent 用完整聊天画布，不叠加跨助手底部导航。
  const showMobileTabBar = !isStandalone && !isTravelMode && pathname !== '/agent'

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent AI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
      </head>
      <body className="h-full flex bg-surface-root">
        {!isTaskMode && !isStandalone && (
          <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        )}
        <main className={`flex-1 flex flex-col min-w-0 ${showMobileTabBar ? 'pb-16 md:pb-0' : ''}`}>
          <AuthGate>
            <ToastProvider>{children}</ToastProvider>
          </AuthGate>
          {/* 仅非助手工作区显示通用移动导航；三种助手通过门户切换，各页提供返回门户入口。 */}
          {showMobileTabBar && <MobileTabBar />}
        </main>
      </body>
    </html>
  )
}
