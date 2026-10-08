'use client'

import { useCallback, useEffect, useRef } from 'react'
import { useCSChatStore } from '@/store/csChat'
import { streamChat, abortChat } from '@/api/chat'
import { getMyPendingAction } from '@/api/cs'
import { nanoid } from 'nanoid'

export function useCSChat() {
  const abortRef = useRef<AbortController | null>(null)
  const requestIdRef = useRef<string>('')

  // #20：抽屉关闭/组件卸载时终止在途流（否则流继续跑完并计费）
  useEffect(() => () => { abortRef.current?.abort() }, [])

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
          // #19：error 帧后主动断开，防连接悬挂（后端可能不再发后续帧）
          controller.abort()
          // #7：闭包比对——期间已发起新请求时不得清掉新请求的状态
          if (useCSChatStore.getState().currentRequestId === requestId) {
            setLoading(false)
          }
          return
        }

        // 退款候选点选（2026-10-08）：clarification 帧的 options 渲染为
        // 可点选订单卡，点击即把选项话术作为用户消息发送（与主图
        // ClarificationCard 同语义）。新提问时 resetStream 已清上一轮。
        if (evt.event === 'clarification') {
          const opts = (evt.data as { options?: { id: string; label: string }[] })
            .options
          useCSChatStore.getState().setCandidateOptions(
            Array.isArray(opts) && opts.length > 0 ? opts : null,
          )
          return
        }

        if (evt.event === 'done') {
          const finalState = useCSChatStore.getState()
          replaceLastAssistant(finalState.deltaText || '(空回答)', sessionId)
          // SSE 缺少 pending_action 表示「状态未变化」；只有显式 null 才清卡。
          // 非空事件只作变更信号，确认 id/version 等字段从 PG 权威接口恢复。
          if (Object.prototype.hasOwnProperty.call(evt.data, 'pending_action')) {
            if (evt.data.pending_action === null) {
              finalState.setPendingAction(sessionId, null)
            } else if (evt.data.pending_action) {
              try {
                const pending = await getMyPendingAction(sessionId)
                if (useCSChatStore.getState().currentRequestId === requestId) {
                  useCSChatStore.getState().setPendingAction(sessionId, pending)
                }
              } catch {
                // SSE 主回答已完成；快照暂不可读时保留本地卡片，打开/切换会话可重试。
              }
            }
          }
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
      // #7：闭包比对——stop A 后立即发 B 时，A 的 finally 异步到达会清掉
      // B 的 loading / currentRequestId。只有自己仍是当前请求时才清态。
      if (useCSChatStore.getState().currentRequestId === requestId) {
        setLoading(false)
        setCurrentRequestId(null)
      }
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
