'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat } from '@/lib/api'
import type { SSEEvent } from '@/lib/types'

export function useSSE() {
  const startStream = useCallback(async (question: string, sessionId: string) => {
    const { addMessage, addThinkingEvent, replaceLastAssistant, setLoading, setError } =
      useChatStore.getState()

    setLoading(true)
    setError(null)

    // User message
    addMessage('user', question)

    // Empty assistant placeholder
    addMessage('assistant', '')

    try {
      const thinkingEvents: SSEEvent[] = []

      for await (const event of streamChat({ question, session_id: sessionId })) {
        thinkingEvents.push(event)
        addThinkingEvent(event)

        if (event.stage === 'done' && event.data.final_answer) {
          replaceLastAssistant(event.data.final_answer, thinkingEvents)
        }
      }
    } catch (err: any) {
      setError(err.message || '请求失败')
      replaceLastAssistant(
        `## 请求失败\n\n${err.message || '未知错误'}`,
      )
    } finally {
      setLoading(false)
    }
  }, [])

  return { startStream }
}
