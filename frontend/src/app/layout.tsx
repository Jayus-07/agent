'use client'

import { useState } from 'react'
import { usePathname } from 'next/navigation'
import Sidebar from '@/components/Sidebar'
import { ToastProvider } from '@/components/shared/Toast'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const pathname = usePathname()

  // /agent 走任务模式：全局控制台导航让位给页面自渲染的 TaskSidebar。
  // 用精确匹配而非 startsWith —— /agent/tasks 仍走原控制台导航，避免扩大影响面。
  const isTaskMode = pathname === '/agent'

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent AI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="h-full flex bg-surface-root">
        {!isTaskMode && (
          <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        )}
        <main className="flex-1 flex flex-col min-w-0"><ToastProvider>{children}</ToastProvider></main>
      </body>
    </html>
  )
}
