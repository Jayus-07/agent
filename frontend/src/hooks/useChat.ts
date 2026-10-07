'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { useSSE } from './useSSE'

export function useSendMessage() {
  const currentId = useChatStore((s) => s.currentId)
  const { startStream, stopStream } = useSSE()

  const send = useCallback(
    async (question: string) => {
      // AI 助手页锁域（2026-10-08）：每条消息带 domain_hint=main——旅游/选品
      // 等域请求不在本页执行，后端改产引导卡送用户去专属页。
      await startStream(question, currentId, 'main')
    },
    [currentId, startStream],
  )

  return { send, stopStream }
}
