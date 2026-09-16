'use client'

import { useState } from 'react'
import { usePathname } from 'next/navigation'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Sidebar from '@/components/layout/Sidebar'
import AuthGate from '@/components/AuthGate'
import { ToastProvider } from '@/components/shared/Toast'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const pathname = usePathname()

  // React Query（2026-09-16 起）：轮询/缓存/去重统一收口，取代手写 setInterval。
  // refetchOnWindowFocus 开启 → 切回前台自动补一次刷新（此前手写在 gateway 页）。
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: true,
          },
        },
      }),
  )

  // /agent 走任务模式：全局控制台导航让位给页面自渲染的 TaskSidebar
  // （12 个业务入口在 TaskSidebar 上半区常驻，会话历史在下半区）。
  // 用精确匹配而非 startsWith —— /agent/tasks 仍走原控制台导航，避免扩大影响面。
  const isTaskMode = pathname === '/agent'
  // 登录页独立呈现：不渲染全局侧边栏（AuthGate 同样对其放行）
  const isLoginPage = pathname.startsWith('/login')

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent 管理端</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="h-full flex bg-surface-root">
        {!isTaskMode && !isLoginPage && (
          <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        )}
        <main className="flex-1 flex flex-col min-w-0">
          <AuthGate>
            <QueryClientProvider client={queryClient}>
              <ToastProvider>{children}</ToastProvider>
            </QueryClientProvider>
          </AuthGate>
        </main>
      </body>
    </html>
  )
}
