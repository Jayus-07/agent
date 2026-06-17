'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { useSSE } from './useSSE'

export function useSendMessage() {
  const currentId = useChatStore((s) => s.currentId)
  const { startStream } = useSSE()

  const send = useCallback(
    async (question: string) => {
      await startStream(question, currentId)
    },
    [currentId, startStream],
  )

  return { send }
}
