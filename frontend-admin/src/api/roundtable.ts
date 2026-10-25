/**
 * Agent 圆桌演示 service
 *
 * 后端为 backend/app/api/routes/agent_roundtable.py（前缀 /roundtable，
 * 经 BFF /api/roundtable 代理）：
 *  - getParticipants → 可选角色与工具箱清单（只读，配置面板数据源）
 *  - streamRoundtable → SSE 圆桌对话流（管理员闸；演示功能聊完即弃）
 *
 * 演示事件协议：meta / status / tool_result / message / done / error / ping。
 * 与主图 SSE v2 契约（lib/types.ts SSEStreamEvent）风格一致但独立类型：
 * message/tool_result 为圆桌专属事件，不混入主图联合类型。
 */

import { fetchRaw, request } from '@/api/client'
import { parseSSEStream } from '@/lib/sse-parser'

export interface RoundtableParticipant {
  route_mode: string
  display_name: string
  label: string
  tools: string[]
}

export interface RoundtableLimits {
  min_participants: number
  max_participants: number
  max_rounds: number
  topic_max_chars: number
  max_total_llm_calls: number
}

export interface RoundtableParticipantsResponse {
  limits: RoundtableLimits
  participants: RoundtableParticipant[]
}

export interface RoundtableMetaEvent {
  request_id: string
  topic: string
  max_rounds: number
  participants: RoundtableParticipant[]
  ts: number
}

export interface RoundtableStatusEvent {
  stage: 'thinking' | 'tool' | 'budget'
  agent?: string
  display_name?: string
  capability?: string
  round?: number
  message: string
  ts: number
}

export interface RoundtableToolResultEvent {
  agent: string
  display_name: string
  capability: string
  ok: boolean
  summary: string
  ts: number
}

export interface RoundtableMessageEvent {
  agent: string
  agent_label: string
  display_name: string
  round: number
  content: string
  ts: number
}

export interface RoundtableDoneEvent {
  elapsed: number
  rounds: number
  message_count: number
  ts: number
}

export interface RoundtableErrorEvent {
  message: string
  ts: number
}

/** 圆桌 SSE 事件联合（演示协议，独立于主图 SSEStreamEvent） */
export type RoundtableStreamEvent =
  | { event: 'meta'; data: RoundtableMetaEvent }
  | { event: 'status'; data: RoundtableStatusEvent }
  | { event: 'tool_result'; data: RoundtableToolResultEvent }
  | { event: 'message'; data: RoundtableMessageEvent }
  | { event: 'ping'; data: { ts: number } }
  | { event: 'done'; data: RoundtableDoneEvent }
  | { event: 'error'; data: RoundtableErrorEvent }

export interface StreamRoundtableParams {
  topic: string
  participants: string[]
  maxRounds: number
}

export const roundtableService = {
  /** 圆桌可选角色清单（只读，走统一 request 出口拿认证/刷新/错误处理） */
  getParticipants: async function (): Promise<RoundtableParticipantsResponse> {
    return request<RoundtableParticipantsResponse>('/api/roundtable/participants')
  },

  /** 发起并消费圆桌 SSE 流；取消用 AbortSignal（停止按钮 / 组件卸载）。 */
  streamRoundtable: async function* (
    params: StreamRoundtableParams,
    options: { signal?: AbortSignal } = {},
  ): AsyncGenerator<RoundtableStreamEvent> {
    const res = await fetchRaw('/api/roundtable/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: params.topic,
        participants: params.participants,
        max_rounds: params.maxRounds,
      }),
      signal: options.signal,
    })
    if (!res.ok || !res.body) {
      const detail = await res.text().catch(() => '')
      throw new Error(`POST /api/roundtable/stream 失败 (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`)
    }
    yield* parseSSEStream(res.body, options.signal) as AsyncGenerator<RoundtableStreamEvent>
  },
}
