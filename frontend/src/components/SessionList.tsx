'use client'

import { useChatStore } from '@/store/chat'
import SessionItem from './SessionItem'

export default function SessionList() {
  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)
  const switchSession = useChatStore((s) => s.switchSession)
  const deleteSession = useChatStore((s) => s.deleteSession)
  const renameSession = useChatStore((s) => s.renameSession)

  if (sessions.length === 0) {
    return (
      <p className="text-xs text-[#8e8e8e] px-2 py-4 text-center">暂无会话</p>
    )
  }

  return (
    <ul className="space-y-0.5">
      {sessions.map((s) => (
        <SessionItem
          key={s.id}
          session={s}
          isActive={s.id === currentId}
          onSelect={() => switchSession(s.id)}
          onDelete={() => deleteSession(s.id)}
          onRename={(title) => renameSession(s.id, title)}
        />
      ))}
    </ul>
  )
}
