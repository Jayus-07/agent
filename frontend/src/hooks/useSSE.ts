'use client'

import { useCallback, useRef } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat, abortChat } from '@/lib/api/chat'
import type { SSEStreamEvent } from '@/lib/api/chat'
import { nanoid } from 'nanoid'

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null)
  const requestIdRef = useRef<string>('')

  const startStream = useCallback(async (question: string, sessionId: string) => {
    // Abort any previous in-flight stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const requestId = nanoid(8)
    requestIdRef.current = requestId

    const store = useChatStore.getState()
    const { addMessage, addStreamEvent, replaceLastAssistant, setLoading, setError, setCurrentRequestId, resetStream } = store

    // 重置上次流式状态
    resetStream()
    setLoading(true)
    setError(null)
    setCurrentRequestId(requestId)

    // 添加占位消息
    addMessage('user', question, sessionId)
    addMessage('assistant', '', sessionId)

    const streamEvents: SSEStreamEvent[] = []
    try {
      for await (const evt of streamChat(
        { question, session_id: sessionId, request_id: requestId },
        controller.signal,
      )) {
        if (controller.signal.aborted) return

        streamEvents.push(evt)
        addStreamEvent(evt, sessionId)

        // error 事件 → 立即持久化到消息内容
        if (evt.event === 'error') {
          replaceLastAssistant(
            `## ${evt.data.message}`,
            sessionId,
          )
          setLoading(false)
          return
        }

        // done 事件 → 将累积的 delta 文本 + sources 写入最终消息
        if (evt.event === 'done') {
          const finalState = useChatStore.getState()
          replaceLastAssistant(
            finalState.deltaText || '(空回答)',
            sessionId,
            evt.data.sources,
          )
        }
      }
    } catch (err: any) {
      if (controller.signal.aborted) return
      setError(err.message || '请求失败')
      // 保留已显示内容，不清空
      const finalState = useChatStore.getState()
      if (finalState.deltaText) {
        replaceLastAssistant(
          finalState.deltaText + `\n\n---\n\n⚠️ **请求中断**: ${err.message || '未知错误'}`,
          sessionId,
        )
      } else {
        replaceLastAssistant(
          `## 请求失败\n\n${err.message || '未知错误'}`,
          sessionId,
        )
      }
    } finally {
      setLoading(false)
      setCurrentRequestId(null)
    }
  }, [])

  /** 停止生成：断开连接 + 发送中止信号 */
  const stopStream = useCallback(async () => {
    const store = useChatStore.getState()
    const sessionId = store.currentId
    const requestId = store.currentRequestId

    // 1) 前端断开 fetch
    abortRef.current?.abort()

    // 2) 通知后端中止
    if (requestId) {
      await abortChat(sessionId, requestId)
    }
  }, [])

  return { startStream, stopStream }
}
