'use client'

import { useState } from 'react'
import { PanelLeftOpen } from 'lucide-react'
import ChatView from '@/components/ChatView'
import HistorySidebar from '@/components/chat/HistorySidebar'

export default function AgentChatPage() {
  const [showHistory, setShowHistory] = useState(true)

  return (
    <div className="flex-1 flex min-h-0 relative">
      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Toggle button */}
        {!showHistory && (
          <button
            onClick={() => setShowHistory(true)}
            className="absolute right-4 top-3 z-10 p-1.5 rounded-lg hover:bg-black/5 text-text-muted transition-colors"
            aria-label="打开分析历史"
            title="分析历史"
          >
            <PanelLeftOpen size={16} />
          </button>
        )}
        <ChatView />
      </div>

      {/* Right sidebar: analysis history */}
      {showHistory && <HistorySidebar onClose={() => setShowHistory(false)} />}
    </div>
  )
}
