import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { Session, Message, ChatMode, SSEStreamEvent } from '@/lib/types'

interface ChatState {
  // — 数据 —
  sessions: Session[]
  currentId: string
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
  /** 历史消息/会话列表加载失败信息。与 error 分开：error 属于当前对话轮次，
   *  混用会让"历史加载失败"显示在聊天区，误导用户以为本次提问出错 */
  historyError: string | null
  /** 当前请求的 request_id（用于中止） */
  currentRequestId: string | null

  // — 计算属性 —
  currentMessages: () => Message[]

  // — 会话操作 —
  newSession: () => string
  switchSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  deleteSession: (id: string) => void
  loadHistory: (sessionId: string) => Promise<void>
  loadSessions: () => Promise<void>

  // — 消息操作 (sessionId 可选，用于 SSE 流固定目标会话) —
  addMessage: (role: 'user' | 'assistant', content: string, sessionId?: string) => void
  addStreamEvent: (evt: SSEStreamEvent, sessionId?: string) => void
  replaceLastAssistant: (content: string, sessionId?: string, sources?: any[]) => void

  // — 状态 —
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setHistoryError: (e: string | null) => void
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
    streamEvents: [],
    currentStatus: '',
    deltaText: '',
    nodeLabels: {},
    isLoading: false,
    error: null,
    historyError: null,
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
        error: null,
      }))
      return s.id
    },

    switchSession: (id) => {
      set({ currentId: id, error: null })
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
    addMessage: (role, content, sessionId) => {
      const msg: Message = {
        id: nanoid(),
        role,
        content,
        timestamp: Date.now(),
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
        return { sessions }
      })
    },

    // — SSE v2: 按事件类型分流更新 —
    //  设计：
    //   - delta 文本不再回写 message.content（P0-4：避免每条 token 触发所有 bubble 重渲染）；
    //     流式文本只写入 store.deltaText，由 StreamingBubble 单独订阅。
    //   - done/error 终态事件清空 message.streamEvents 和 store.streamEvents（P0-5：
    //     防止长会话累积撑爆内存；OOM 风险）。
    //   - 通用事件（status/log/delta）按 200 上限环形追加；超长后丢弃最老。
    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        const isCurrentSession = !sessionId || sessionId === state.currentId

        const MAX_STREAM_EVENTS = 200
        let storeEvents = state.streamEvents
        if (isCurrentSession) {
          if (evt.event === 'meta' || evt.event === 'done') {
            // meta 是握手事件，不入流；done 清空队列（流结束 = 全部渲染完）
            if (evt.event === 'done') storeEvents = []
          } else {
            storeEvents = storeEvents.length >= MAX_STREAM_EVENTS
              ? [...storeEvents.slice(storeEvents.length - MAX_STREAM_EVENTS + 1), evt]
              : [...storeEvents, evt]
          }
        }

        let deltaText = state.deltaText
        let currentStatus = state.currentStatus
        let nodeLabels = state.nodeLabels
        const isTerminal = evt.event === 'done' || evt.event === 'error'

        if (evt.event === 'meta') {
          nodeLabels = evt.data.node_labels
        } else if (evt.event === 'status') {
          currentStatus = evt.data.node
        } else if (evt.event === 'delta') {
          deltaText = state.deltaText + evt.data.content
        } else if (isTerminal) {
          // 终态清空状态节点：否则 error/中断路径（无 done 事件）下
          // StatusBar 会残留最后一个阶段标签（如“✍️ 生成回复”）
          currentStatus = ''
        }

        // 实时更新最后一条 assistant 消息的 streamEvents
        // - 终态：清空该字段（释放内存）
        // - 其他事件：环形追加（200 上限）
        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const msgs = s.messages.map((m, idx) => {
            if (idx !== s.messages.length - 1 || m.role !== 'assistant') return m
            if (isTerminal) {
              if (!m.streamEvents || m.streamEvents.length === 0) return m
              return { ...m, streamEvents: [] }
            }
            const cur = m.streamEvents || []
            const next = cur.length >= MAX_STREAM_EVENTS
              ? [...cur.slice(cur.length - MAX_STREAM_EVENTS + 1), evt]
              : [...cur, evt]
            return { ...m, streamEvents: next }
          })
          return { ...s, messages: msgs, updatedAt: Date.now() }
        })

        return { sessions, streamEvents: storeEvents, deltaText, currentStatus, nodeLabels }
      })
    },

    setCurrentRequestId: (id) => set({ currentRequestId: id }),

    resetStream: () => set({ streamEvents: [], currentStatus: '', deltaText: '', currentRequestId: null }),

    replaceLastAssistant: (content, sessionId, sources) => {
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
              sources: sources || msgs[lastIdx].sources,
              timestamp: Date.now(),
            }
          }
          return { ...s, messages: msgs, updatedAt: Date.now() }
        }),
      }))
    },

    // —— 从后端加载持久化会话历史消息 ——
    loadHistory: async (sessionId: string) => {
      try {
        const { getSessionMessages } = await import('@/lib/api/memory')
        const msgs = await getSessionMessages(sessionId)
        set({ historyError: null })
        if (!msgs || msgs.length === 0) return

        const restored: Message[] = msgs.map((m: any) => ({
          id: nanoid(),
          role: m.role,
          content: m.content,
          timestamp: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
        }))

        set((state) => {
          const exists = state.sessions.some((s) => s.id === sessionId)
          if (exists) {
            return {
              sessions: state.sessions.map((s) =>
                s.id === sessionId ? { ...s, messages: restored } : s,
              ),
            }
          }
          // 远程会话不在本地 store（侧边栏用自己的缓存渲染，不入 store）——
          // 不存在则新建会话实体，否则消息永远挂不上去（会话恢复失效的根因）。
          const firstUser = restored.find((m) => m.role === 'user')
          const title = firstUser
            ? firstUser.content.slice(0, 30) + (firstUser.content.length > 30 ? '...' : '')
            : '历史会话'
          return {
            sessions: [
              {
                id: sessionId,
                title,
                mode: 'chat' as const,
                messages: restored,
                createdAt: restored[0]?.timestamp ?? Date.now(),
                updatedAt: Date.now(),
              },
              ...state.sessions,
            ],
            currentId: sessionId,
          }
        })
      } catch (e) {
        // 不再静默：记忆库故障必须留下痕迹，否则历史消息凭空消失且无从排查
        set({ historyError: e instanceof Error ? e.message : '加载历史消息失败' })
      }
    },

    // —— 从后端加载持久化会话 ——
    loadSessions: async () => {
      try {
        const { listSessions } = await import('@/lib/api')
        const remote = await listSessions()
        if (remote.length === 0) return

        const state = get()
        const existingIds = new Set(state.sessions.map((s) => s.id))

        const remoteSessions = remote
          .filter((m) => !existingIds.has(m.session_id))
          .map((m) => ({
            id: m.session_id,
            title: m.title,
            mode: 'chat' as const,
            messages: [] as Message[],
            createdAt: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
            updatedAt: m.updated_at ? new Date(m.updated_at).getTime() : Date.now(),
          }))

        if (remoteSessions.length > 0) {
          set((state) => ({
            sessions: [...remoteSessions, ...state.sessions.filter((s) => s.messages.length > 0)],
          }))
        }
        set({ historyError: null })
      } catch (e) {
        set({ historyError: e instanceof Error ? e.message : '加载会话列表失败' })
      }
    },

    // —— 状态 ——
    setLoading: (v) => set({ isLoading: v }),
    setError: (e) => set({ error: e }),
    setHistoryError: (e) => set({ historyError: e }),
  }
})
