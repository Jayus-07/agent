'use client'

import { MessageSquare, Database, Library, FileText } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { useRouter } from 'next/navigation'
import type { ChatMode } from '@/lib/types'

const modes: { key: ChatMode; label: string; icon: React.ReactNode; path: string }[] = [
  { key: 'chat',   label: '对话',   icon: <MessageSquare size={15} />, path: '/' },
  { key: 'sql',    label: 'SQL',    icon: <Database size={15} />,      path: '/sql' },
  { key: 'rag',    label: '知识库', icon: <Library size={15} />,       path: '/rag' },
  { key: 'report', label: '报告',   icon: <FileText size={15} />,      path: '/report' },
]

export default function ModeTabs() {
  const mode = useChatStore((s) => s.mode)
  const setMode = useChatStore((s) => s.setMode)
  const newSession = useChatStore((s) => s.newSession)
  const router = useRouter()

  function handleSwitch(key: ChatMode, path: string) {
    setMode(key)
    newSession(key)
    router.push(path)
  }

  return (
    <div className="flex px-3 py-2 gap-0.5">
      {modes.map((m) => (
        <button
          key={m.key}
          onClick={() => handleSwitch(m.key, m.path)}
          className={`flex items-center gap-1 px-2 py-1.5 rounded-md text-xs transition-colors ${
            mode === m.key
              ? 'bg-[#2f2f2f] text-[#ececec]'
              : 'text-[#8e8e8e] hover:bg-[#2f2f2f] hover:text-[#b4b4b4]'
          }`}
          title={m.label}
        >
          {m.icon}
          <span className="hidden md:inline">{m.label}</span>
        </button>
      ))}
    </div>
  )
}
