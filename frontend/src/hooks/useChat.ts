'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { useSSE } from './useSSE'

export function useSendMessage() {
  const currentId = useChatStore((s) => s.currentId)
  const { startStream, stopStream } = useSSE()

  const send = useCallback(
    async (question: string) => {
      // 产品身份由服务端注册的 /chat/agent 路由固定。
      await startStream(question, currentId)
    },
    [currentId, startStream],
  )

  return { send, stopStream }
}
