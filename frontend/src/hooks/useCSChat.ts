'use client'

import { useCallback, useRef } from 'react'
import { useCSChatStore } from '@/store/csChat'
import { streamChat, abortChat } from '@/api/chat'
import { nanoid } from 'nanoid'

export function useCSChat() {
  const abortRef = useRef<AbortController | null>(null)
  const requestIdRef = useRef<string>('')

  const startStream = useCallback(async (question: string, sessionId: string) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const requestId = nanoid(8)
    requestIdRef.current = requestId

    const store = useCSChatStore.getState()
    const {
      addMessage, addStreamEvent, replaceLastAssistant,
      setLoading, setError, setCurrentRequestId, resetStream,
    } = store

    resetStream()
    setLoading(true)
    setError(null)
    setCurrentRequestId(requestId)

    addMessage('user', question, sessionId)
    addMessage('assistant', '', sessionId)

    try {
      for await (const evt of streamChat(
        {
          question,
          session_id: sessionId,
          request_id: requestId,
          // 客服窗口锁域（2026-09-18）：用户已显式进入客服窗口，每条消息
          // 直接进客服管线，不再重新判域（否则"下周去大阪怎么玩"会漏进
          // 旅游域图硬答）也不受灰度分组影响。
          domain_hint: "customer_service",
        },
        controller.signal,
      )) {
        if (controller.signal.aborted) return

        addStreamEvent(evt, sessionId)

        if (evt.event === 'error') {
          replaceLastAssistant(`## ${evt.data.message}`, sessionId)
          setLoading(false)
          return
        }

        if (evt.event === 'done') {
          const finalState = useCSChatStore.getState()
          replaceLastAssistant(finalState.deltaText || '(空回答)', sessionId)
          // P3.1: done 帧 pending_action → 确认卡片（非空时抽屉渲染 CSConfirmCard；
          // 无则显式清，防止上一轮残留）。startStream 开头的 resetStream 也会清。
          const pa = evt.data.pending_action
          finalState.setPendingProposal(
            pa ? { proposalText: pa.proposal_text, actionType: pa.action_type } : null,
          )
          // 持久化由后端 CS 管线统一完成（runner end_turn → record_cs_turn →
          // cs_conversations/cs_messages），前端不再调 /chat/messages 二次写入 ——
          // 双写会把客服会话漏进主对话记忆库，侧栏「任务」列表因此混入
          // 「我想转接人工客服」等客服条目（与 useSSE 同一批次移除的双写残留）。
        }
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return
      const message = err instanceof Error ? err.message : '请求失败'
      setError(message)
      const finalState = useCSChatStore.getState()
      if (finalState.deltaText) {
        replaceLastAssistant(
          finalState.deltaText + `\n\n---\n\n⚠️ **请求中断**: ${message}`,
          sessionId,
        )
      } else {
        replaceLastAssistant(`## 请求失败\n\n${message}`, sessionId)
      }
    } finally {
      setLoading(false)
      setCurrentRequestId(null)
    }
  }, [])

  const stopStream = useCallback(async () => {
    const store = useCSChatStore.getState()
    const sessionId = store.currentId
    const requestId = store.currentRequestId
    abortRef.current?.abort()
    if (requestId) {
      await abortChat(sessionId, requestId)
    }
  }, [])

  return { startStream, stopStream }
}
