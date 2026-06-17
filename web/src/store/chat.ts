import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { Session, Message, ChatMode, SSEEvent } from '@/lib/types'

interface ChatState {
  // — 数据 —
  sessions: Session[]
  currentId: string
  /** 当前正在进行的 SSE 思考事件 */
  thinking: SSEEvent[]
  isLoading: boolean
  error: string | null

  // — 计算属性 —
  currentMessages: () => Message[]

  // — 会话操作 —
  newSession: () => string
  switchSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  deleteSession: (id: string) => void

  // — 消息操作 (sessionId 可选，用于 SSE 流固定目标会话) —
  addMessage: (role: 'user' | 'assistant', content: string, thinking?: SSEEvent[], sessionId?: string) => void
  addThinkingEvent: (event: SSEEvent, sessionId?: string) => void
  replaceLastAssistant: (content: string, thinking?: SSEEvent[], sessionId?: string) => void

  // — 状态 —
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
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
    isLoading: false,
    error: null,

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
        // 如果指定了 sessionId 且与当前不同，不污染当前 thinking 显示
        if (sessionId && sessionId !== state.currentId) {
          return {} // 静默写入消息 thinking 字段（由 replaceLastAssistant 完成）
        }
        return { thinking: [...state.thinking, event] }
      })
    },

    replaceLastAssistant: (content, thinking, sessionId) => {
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
