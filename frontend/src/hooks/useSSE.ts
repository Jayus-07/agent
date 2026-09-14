'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat, abortChat } from '@/lib/api/chat'
import { invalidateSessionsCache } from '@/lib/sessions-cache'
import { getSelectedDepartment } from '@/lib/department'
import { nanoid } from 'nanoid'

// 模块级 abort controller：send / regenerate / stopStream 共用一份，
// 多组件各自调用 useSSE() 也操作同一条在途流，中止路由不会分裂
let activeController: AbortController | null = null

export function useSSE() {
  /** 执行一轮流式请求：重置流状态 → assistant 占位 → 消费 SSE 事件。
   *  不追加 user 消息——消息编排由调用方决定（send 先追加提问，regenerate 复用已有提问）。 */
  const runStream = useCallback(async (question: string, sessionId: string) => {
    // Abort any previous in-flight stream
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller

    const requestId = nanoid(8)

    const store = useChatStore.getState()
    const { addMessage, addStreamEvent, replaceLastAssistant, setLoading, setError, setCurrentRequestId, resetStream } = store

    // 重置上次流式状态
    resetStream()
    setLoading(true)
    setError(null)
    setCurrentRequestId(requestId)

    // 添加占位消息
    addMessage('assistant', '', sessionId)

    try {
      for await (const evt of streamChat(
        { question, session_id: sessionId, request_id: requestId,
          department: getSelectedDepartment() || undefined },
        controller.signal,
      )) {
        if (controller.signal.aborted) return

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

        // done 事件 → 将累积的 delta 文本 + sources + 思考链写入最终消息
        if (evt.event === 'done') {
          const finalState = useChatStore.getState()
          replaceLastAssistant(
            finalState.deltaText || '(空回答)',
            sessionId,
            evt.data.sources,
            evt.data.usage,
            finalState.thinkingText,
            finalState.thinkingSeconds ?? undefined,
          )
          // 持久化由后端 end_turn 统一完成（finally 中 save_turn），
          // 前端不再调 /chat/messages 二次写入 —— 双写会让历史恢复时消息重复、
          // 下一轮注入的上下文也翻倍。
          // 失效会话列表缓存并通知侧栏刷新，让新会话/更新时间立即出现在历史里
          invalidateSessionsCache()
          useChatStore.getState().bumpSessionsVersion()
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

  const startStream = useCallback(async (question: string, sessionId: string) => {
    useChatStore.getState().addMessage('user', question, sessionId)
    await runStream(question, sessionId)
  }, [runStream])

  /** 重新生成最后一条回答：移除尾部 assistant 消息，用最近一条 user 提问重跑一轮。
   *  加载中 / 尾部不是 assistant 回答 / 找不到提问时静默忽略。 */
  const regenerate = useCallback(async (sessionId: string) => {
    const store = useChatStore.getState()
    if (store.isLoading) return
    const session = store.sessions.find((s) => s.id === sessionId)
    if (!session || session.messages.length === 0) return
    if (session.messages[session.messages.length - 1].role !== 'assistant') return

    let question = ''
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') {
        question = session.messages[i].content
        break
      }
    }
    if (!question) return

    store.removeLastAssistant(sessionId)
    await runStream(question, sessionId)
  }, [runStream])

  /** 停止生成：断开连接 + 发送中止信号 */
  const stopStream = useCallback(async () => {
    const store = useChatStore.getState()
    const sessionId = store.currentId
    const requestId = store.currentRequestId

    // 1) 前端断开 fetch
    activeController?.abort()

    // 2) 通知后端中止
    if (requestId) {
      await abortChat(sessionId, requestId)
    }
  }, [])

  return { startStream, stopStream, regenerate }
}
