'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat, abortChat } from '@/api/chat'
import { apiErrorFromEnvelope } from '@/api/client'
import { invalidateSessionsCache } from '@/lib/sessions-cache'
import { getSelectedDepartment } from '@/lib/department'
import { nanoid } from 'nanoid'

// 模块级 abort controller：send / regenerate / stopStream 共用一份，
// 多组件各自调用 useSSE() 也操作同一条在途流，中止路由不会分裂
let activeController: AbortController | null = null

export function useSSE() {
  /** 执行一轮流式请求：重置流状态 → assistant 占位 → 消费 SSE 事件。
   *  不追加 user 消息——消息编排由调用方决定（send 先追加提问，regenerate 复用已有提问）。 */
  const runStream = useCallback(async (
    question: string,
    sessionId: string,
    // 会话级模型覆盖（B.9 决策②）；null / 缺省 = 走后端全局默认
    modelOverride?: string | null,
  ) => {
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
          department: getSelectedDepartment() || undefined,
          idempotency_key: requestId,
          // 会话级模型覆盖：只有本次会话显式选过模型才带，否则用后端全局默认
          model: modelOverride || undefined },
        controller.signal,
      )) {
        if (controller.signal.aborted) return

        // done 前快照执行过程（done 事件本身会清空 streamEvents，防 OOM）
        const isDone = evt.event === 'done'
        const snapshot = isDone
          ? {
              streamEvents: useChatStore.getState().streamEvents,
              todoItems: useChatStore.getState().todoItems,
              nodeLabels: useChatStore.getState().nodeLabels,
            }
          : null

        addStreamEvent(evt, sessionId)

        // error 事件 → 立即持久化到消息内容
        if (evt.event === 'error') {
          setError(apiErrorFromEnvelope(evt.data))
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
          // 固化执行过程快照（完成态常驻行回看用）；done 前已截取，此处 streamEvents 已被终态清空
          if (snapshot) {
            useChatStore.getState().attachTrace(sessionId, {
              elapsed: evt.data.elapsed,
              streamEvents: snapshot.streamEvents,
              todoItems: snapshot.todoItems,
              nodeLabels: snapshot.nodeLabels,
            })
          }
          replaceLastAssistant(
            finalState.deltaText || '(空回答)',
            sessionId,
            evt.data.sources,
            evt.data.usage,
            finalState.thinkingText,
            finalState.thinkingSeconds ?? undefined,
            evt.data.trace_id,
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
      // X3 语义化：存原始异常对象（streamChat 抛错带 status，ErrorCard 可解析
      // 出 kind + 行动指引）；err.message 作为流中断消息内容的兜底文案
      setError(err)
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
    await runStream(question, sessionId, useChatStore.getState().sessionModel)
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
    // 重新生成沿用同一模型：否则「重新生成」会换模型，两次结果不可比（B.9）
    await runStream(question, sessionId, store.sessionModel)
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
