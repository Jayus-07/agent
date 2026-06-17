'use client'

import { useState } from 'react'
import Sidebar from '@/components/Sidebar'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)

  return (
    <html lang="zh-CN">
      <head>
        <title>Agent AI</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body className="h-full flex">
        {/* 侧边栏 */}
        <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />

        {/* 主内容区 */}
        <main className="flex-1 flex flex-col min-w-0">
          {/* 顶栏（移动端菜单按钮 + 标题） */}
          <header className="h-12 flex items-center px-4 border-b border-[#3f3f3f] shrink-0">
            <button
              onClick={() => setSidebarOpen((v) => !v)}
              className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors md:hidden"
              aria-label="切换侧边栏"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 12h18M3 6h18M3 18h18" />
              </svg>
            </button>
            <span className="text-sm text-[#b4b4b4] ml-2">Agent Platform</span>
          </header>

          {children}
        </main>
      </body>
    </html>
  )
}
