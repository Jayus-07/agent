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

  // 助手与旅游工作区使用页面自带导航，不叠加通用控制台侧栏。
  const isTravelMode = pathname === '/travel' || pathname.startsWith('/travel/')
  const isTaskMode = pathname === '/agent' || isTravelMode
  // 门户、登录/注册和用户智能客服页使用独立布局。
  // cs-workstation-v2 P8：/customer-service 指向 cs2 重建页（旧过渡路由
  // /customer-service-v2 已删除），商城两页（/mall、/my-orders）为独立页。
  const isStandalone =
    pathname === '/login' ||
    pathname === '/register' ||
    pathname === '/' ||
    pathname === '/customer-service' ||
    pathname === '/mall' ||
    pathname === '/my-orders'
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
          {showMobileTabBar && <MobileTabBar />}
        </main>
      </body>
    </html>
  )
}
