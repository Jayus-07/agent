'use client'

import { useCallback, useRef } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat } from '@/lib/api'
import type { SSEEvent } from '@/lib/types'

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null)

  const startStream = useCallback(async (question: string, sessionId: string) => {
    // Abort any previous in-flight stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const store = useChatStore.getState()
    const { addMessage, addThinkingEvent, replaceLastAssistant, setLoading, setError } = store

    setLoading(true)
    setError(null)

    // All mutations pinned to sessionId — survives concurrent tab switches
    addMessage('user', question, undefined, sessionId)
    addMessage('assistant', '', undefined, sessionId)

    const thinkingEvents: SSEEvent[] = []
    try {

      for await (const event of streamChat({ question, session_id: sessionId }, controller.signal)) {
        // Stop processing if aborted
        if (controller.signal.aborted) return

        thinkingEvents.push(event)
        addThinkingEvent(event, sessionId)

        // Backend error event → persist as content immediately
        if (event.stage === 'error') {
          replaceLastAssistant(
            `## ${event.label || '系统错误'}\n\n${event.message || '未知错误'}`,
            thinkingEvents,
            sessionId,
          )
          setLoading(false)
          return
        }

        if (event.stage === 'done' && event.data.final_answer) {
          replaceLastAssistant(event.data.final_answer, thinkingEvents, sessionId)
        }
      }
    } catch (err: any) {
      if (controller.signal.aborted) return
      setError(err.message || '请求失败')
      // Preserve accumulated thinkingEvents so user can see what happened before failure
      replaceLastAssistant(
        `## 请求失败\n\n${err.message || '未知错误'}`,
        thinkingEvents.length > 0 ? thinkingEvents : undefined,
        sessionId,
      )
    } finally {
      setLoading(false)
    }
  }, [])

  return { startStream }
}
