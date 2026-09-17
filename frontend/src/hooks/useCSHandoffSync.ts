'use client'

/**
 * useCSHandoffSync — 用户侧人工介入同步（2026-09-17 人工介入 v2）
 *
 * 修复点：此前用户端没有任何接收坐席消息的通道（SSE 只覆盖单轮问答流，
 * 坐席回复走独立 HTTP 接口落库），setHandoffState 也无人调用 ——
 * 转人工后 CSHandoffCard 永远不出现、坐席回复用户永远看不到。
 *
 * 机制：抽屉打开期间 2s 轮询 GET /api/cs/conversations/{session}/messages?since_id
 *   - human_agent 消息 → role='agent' 气泡（用户/助手落库消息与本地回显重复，跳过）
 *   - handoff_state → CSHandoffCard（waiting_human→waiting 等）
 *   - 工单消失（坐席关闭/无工单）：上一状态是 active 时显示「已结束」
 * 后续演进：换 WS 订阅（与坐席工作台同一 Hub），轮询仅兜底。
 */
import { useEffect, useRef } from 'react'
import { fetchRaw } from '@/api/client'
import { useCSChatStore, type CSMessage } from '@/store/csChat'
import type { CSHandoffState } from '@/components/cs/constants'

interface HandoffMessageDTO {
  message_id: string
  sender_type: string
  content: string
  content_type?: string
  created_at: string
}

interface HandoffMessagesResponse {
  conversation_id: string
  handoff_state: string
  last_id: number
  messages: HandoffMessageDTO[]
}

const STATE_MAP: Record<string, CSHandoffState> = {
  handoff_requested: 'requested',
  waiting_human: 'waiting',
  human_active: 'active',
}

export function useCSHandoffSync(sessionId: string, enabled: boolean) {
  const sinceIdRef = useRef(0)
  const lastStateRef = useRef<CSHandoffState>('none')

  useEffect(() => {
    if (!enabled || !sessionId) return
    sinceIdRef.current = 0
    lastStateRef.current = 'none'

    let alive = true
    const tick = async () => {
      try {
        const res = await fetchRaw(
          `/api/cs/conversations/${encodeURIComponent(sessionId)}/messages?since_id=${sinceIdRef.current}`,
        )
        if (!alive || !res.ok) return
        const data = (await res.json()) as HandoffMessagesResponse

        const { setHandoffState, addMessage } = useCSChatStore.getState()

        // 坐席消息入列（历史补拉 + 增量都走这里；id=message_id 幂等，
        // appendAgentMessage 按 id 去重——切换会话/水合恢复后 since_id 归零
        // 会重拉全量，靠去重防重复气泡）
        const agentMsgs = data.messages.filter((m) => m.sender_type === 'human_agent')
        for (const m of agentMsgs) {
          const msg: CSMessage = {
            id: m.message_id,
            role: 'agent',
            content: m.content,
            timestamp: m.created_at ? Date.parse(m.created_at) || Date.now() : Date.now(),
            csNodes: [],
          }
          appendAgentMessage(sessionId, msg)
        }
        if (data.last_id > sinceIdRef.current) sinceIdRef.current = data.last_id

        // 状态映射：'none'（无进行中工单）时，刚从 active 消失 → 显示「已结束」
        let mapped = STATE_MAP[data.handoff_state]
        if (!mapped) {
          mapped = lastStateRef.current === 'active' ? 'closed' : 'none'
        }
        if (mapped !== 'none' && mapped !== 'closed') {
          lastStateRef.current = mapped
        }
        setHandoffState(mapped)
      } catch {
        // 静默重试
      }
    }

    tick()
    const timer = setInterval(tick, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [sessionId, enabled])
}

/** 直接写 store：绕过 addMessage（agent 消息不应抢占会话标题）；按 message_id 幂等去重 */
function appendAgentMessage(
  sessionId: string,
  msg: CSMessage,
): void {
  useCSChatStore.setState((state) => {
    const sid = sessionId || state.currentId
    return {
      sessions: state.sessions.map((s) =>
        s.id === sid
          ? s.messages.some((m) => m.id === msg.id)
            ? s
            : {
                ...s,
                messages: [...s.messages, msg],
                updatedAt: Date.now(),
              }
          : s,
      ),
    }
  })
}
