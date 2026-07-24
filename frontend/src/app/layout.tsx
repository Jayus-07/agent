'use client'

import { useState } from 'react'
import Sidebar from '@/components/Sidebar'
import { ToastProvider } from '@/components/shared/Toast'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent AI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="h-full flex bg-surface-root">
        <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        <main className="flex-1 flex flex-col min-w-0"><ToastProvider>{children}</ToastProvider></main>
      </body>
    </html>
  )
}
