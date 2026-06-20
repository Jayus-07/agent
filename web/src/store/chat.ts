import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { Session, Message, ChatMode, SSEEvent, SSEStreamEvent } from '@/lib/types'

interface ChatState {
  // — 数据 —
  sessions: Session[]
  currentId: string
  /** @deprecated 使用 streamEvents 替代 */
  thinking: SSEEvent[]
  /** SSE v2 流式事件累积 */
  streamEvents: SSEStreamEvent[]
  /** 当前宏观状态节点名（StatusBar 消费） */
  currentStatus: string
  /** 流式 delta 累积文本（ChatContent 消费） */
  deltaText: string
  /** node → emoji 映射表（meta 事件下发） */
  nodeLabels: Record<string, string>
  isLoading: boolean
  error: string | null
  /** 当前请求的 request_id（用于中止） */
  currentRequestId: string | null

  // — 计算属性 —
  currentMessages: () => Message[]

  // — 会话操作 —
  newSession: () => string
  switchSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  deleteSession: (id: string) => void

  // — 消息操作 (sessionId 可选，用于 SSE 流固定目标会话) —
  addMessage: (role: 'user' | 'assistant', content: string, thinking?: SSEEvent[], sessionId?: string) => void
  addStreamEvent: (evt: SSEStreamEvent, sessionId?: string) => void
  addThinkingEvent: (event: SSEEvent, sessionId?: string) => void
  replaceLastAssistant: (content: string, thinking?: SSEEvent[], sessionId?: string, sources?: any[]) => void

  // — 状态 —
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setCurrentRequestId: (id: string | null) => void
  resetStream: () => void
}

function createSession(): Session {
  const id = nanoid()
  return {
    id,
    title: '新对话',
    mode: 'chat',
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  }
}

/** 返回目标 session id：显式传入者优先，否则用当前会话 */
function targetId(state: ChatState, sid?: string): string {
  return sid ?? state.currentId
}

export const useChatStore = create<ChatState>((set, get) => {
  const initialSession = createSession()
  return {
    sessions: [initialSession],
    currentId: initialSession.id,
    thinking: [],
    streamEvents: [],
    currentStatus: '',
    deltaText: '',
    nodeLabels: {},
    isLoading: false,
    error: null,
    currentRequestId: null,

    // —— 计算属性 ——
    currentMessages: () => {
      const s = get().sessions.find((s) => s.id === get().currentId)
      return s?.messages ?? []
    },

    // —— 会话 ——
    newSession: () => {
      const s = createSession()
      set((state) => ({
        sessions: [s, ...state.sessions],
        currentId: s.id,
        thinking: [],
        error: null,
      }))
      return s.id
    },

    switchSession: (id) => {
      set({ currentId: id, thinking: [], error: null })
    },

    renameSession: (id, title) => {
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.id === id ? { ...s, title, updatedAt: Date.now() } : s
        ),
      }))
    },

    deleteSession: (id) => {
      set((state) => {
        const remaining = state.sessions.filter((s) => s.id !== id)
        if (remaining.length === 0) {
          const fallback = createSession()
          return { sessions: [fallback], currentId: fallback.id }
        }
        return {
          sessions: remaining,
          currentId: state.currentId === id ? remaining[0].id : state.currentId,
        }
      })
    },

    // —— 消息 ——
    addMessage: (role, content, thinking, sessionId) => {
      const msg: Message = {
        id: nanoid(),
        role,
        content,
        timestamp: Date.now(),
        thinking,
      }
      set((state) => {
        const sid = targetId(state, sessionId)
        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const title = s.messages.length === 0 && role === 'user'
            ? content.slice(0, 30) + (content.length > 30 ? '...' : '')
            : s.title
          return {
            ...s,
            title,
            messages: [...s.messages, msg],
            updatedAt: Date.now(),
          }
        })
        return { sessions, thinking: sessionId ? state.thinking : [] }
      })
    },

    addThinkingEvent: (event, sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        // 实时更新最后一条 assistant 消息的 thinking 字段（流式进度实时可见）
        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === 'assistant') {
            msgs[lastIdx] = {
              ...msgs[lastIdx],
              thinking: [...(msgs[lastIdx].thinking || []), event],
            }
          }
          return { ...s, messages: msgs, updatedAt: Date.now() }
        })
        // 同时更新全局 thinking（兼容旧逻辑）
        const thinking = sessionId && sessionId !== state.currentId
          ? state.thinking
          : [...state.thinking, event]
        return { sessions, thinking }
      })
    },

    // — SSE v2: 按事件类型分流更新 —
    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        const events = sessionId && sessionId !== state.currentId
          ? state.streamEvents
          : [...state.streamEvents, evt]

        let deltaText = state.deltaText
        let currentStatus = state.currentStatus
        const nodeLabels = state.nodeLabels

        switch (evt.event) {
          case 'meta':
            // 握手：接收 node_labels 映射表
            return {
              streamEvents: events,
              nodeLabels: evt.data.node_labels,
            }

          case 'status':
            // 状态切换：只存 node 名，前端自行映射
            currentStatus = evt.data.node
            break

          case 'delta':
            // 流式内容：累积 delta 文本
            deltaText = state.deltaText + evt.data.content
            break

          // log / done / error 仅追加到 streamEvents 数组
        }

        // 实时更新最后一条 assistant 消息的 streamEvents
        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === 'assistant') {
            msgs[lastIdx] = {
              ...msgs[lastIdx],
              streamEvents: [...(msgs[lastIdx].streamEvents || []), evt],
              content: evt.event === 'delta' ? msgs[lastIdx].content + evt.data.content : msgs[lastIdx].content,
            }
          }
          return { ...s, messages: msgs, updatedAt: Date.now() }
        })

        return { sessions, streamEvents: events, deltaText, currentStatus }
      })
    },

    setCurrentRequestId: (id) => set({ currentRequestId: id }),

    resetStream: () => set({ streamEvents: [], currentStatus: '', deltaText: '', currentRequestId: null }),

    replaceLastAssistant: (content, thinking, sessionId, sources) => {
      set((state) => ({
        sessions: state.sessions.map((s) => {
          const sid = targetId(state, sessionId)
          if (s.id !== sid) return s
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === 'assistant') {
            msgs[lastIdx] = {
              ...msgs[lastIdx],
              content,
              thinking,
              sources: sources || msgs[lastIdx].sources,
              timestamp: Date.now(),
            }
          }
          return { ...s, messages: msgs, updatedAt: Date.now() }
        }),
      }))
    },

    // —— 状态 ——
    setLoading: (v) => set({ isLoading: v }),
    setError: (e) => set({ error: e }),
  }
})
