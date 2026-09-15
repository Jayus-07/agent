import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { Session, Message, ChatMode, SSEStreamEvent, TodoItem, TokenUsage } from '@/lib/types'
import { isTerminalEvent, reduceStreamCore } from '@/store/stream-reduce'

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
  /** 思考链累积文本（thinking 事件，"已思考"折叠面板消费）。
   *  chat 私有字段：csChat 无思考链，不进共享归约 stream-reduce */
  thinkingText: string
  /** 思考耗时（秒）：首条 delta 到达时定格；null = 未产生过思考链或思考被中止 */
  thinkingSeconds: number | null
  /** 思考起始时间戳（内部记账：首条 thinking 事件写入，用于算耗时） */
  thinkingStartAt: number
  /** node → emoji 映射表（meta 事件下发） */
  nodeLabels: Record<string, string>
  isLoading: boolean
  error: string | null
  /** 历史消息/会话列表加载失败信息。与 error 分开：error 属于当前对话轮次，
   *  混用会让"历史加载失败"显示在聊天区，误导用户以为本次提问出错 */
  historyError: string | null
  /** 当前请求的 request_id（用于中止） */
  currentRequestId: string | null
  /** 会话列表刷新信号：SSE done 后自增，HistorySidebar 监听它自动重新拉取 */
  sessionsVersion: number
  /** 任务列表快照（todo 事件，全量替换）。chat 私有：csChat 无 planner，不进共享归约 */
  todoItems: TodoItem[]
  /** 流中用量（usage 事件，supervisor 每轮透出的轮内累计；done 后固化进 message.usage） */
  streamUsage: TokenUsage | null
  /** 工具产出文件（file 事件展开为文件级记录，按路径去重、新的覆盖旧的） */
  fileOps: { path: string; node: string; step_id: string; ts: number }[]

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
  removeLastAssistant: (sessionId?: string) => void
  replaceLastAssistant: (content: string, sessionId?: string, sources?: any[], usage?: import('@/lib/types').TokenUsage,
    thinking?: string, thinkingSeconds?: number) => void

  /** done 时固化执行过程快照到尾部 assistant 消息（CompletionLine 回看用） */
  attachTrace: (sessionId: string, trace: import('@/lib/types').AgentTrace) => void

  // — 状态 —
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setHistoryError: (e: string | null) => void
  setCurrentRequestId: (id: string | null) => void
  bumpSessionsVersion: () => void
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
    thinkingText: '',
    thinkingSeconds: null,
    thinkingStartAt: 0,
    nodeLabels: {},
    isLoading: false,
    error: null,
    historyError: null,
    currentRequestId: null,
    sessionsVersion: 0,
    todoItems: [],
    streamUsage: null,
    fileOps: [],

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
    //     流式文本只写入 store.deltaText，由 StreamingContent 单独订阅。
    //   - 公共字段（deltaText/currentStatus/nodeLabels）归约走 stream-reduce.ts
    //     共享实现，与 csChat 保持一致。
    //   - done/error 终态事件清空 message.streamEvents 和 store.streamEvents（P0-5：
    //     防止长会话累积撑爆内存；OOM 风险）。
    //   - 通用事件（status/log/delta）按 200 上限环形追加；超长后丢弃最老。
    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        const isCurrentSession = !sessionId || sessionId === state.currentId

        // —— chat 私有：思考链累积（不进共享归约 stream-reduce，csChat 无此字段）——
        let thinkingText = state.thinkingText
        let thinkingSeconds = state.thinkingSeconds
        let thinkingStartAt = state.thinkingStartAt
        if (isCurrentSession) {
          if (evt.event === 'thinking') {
            if (!thinkingStartAt) thinkingStartAt = Date.now()
            thinkingText += evt.data.content
          } else if (evt.event === 'delta' && thinkingText && thinkingSeconds === null) {
            // 首块回答到达 → 思考阶段定格（秒）；下限 1s 避免亚秒抖动
            thinkingSeconds = Math.max(1, Math.round((Date.now() - thinkingStartAt) / 1000))
          }
        }

        // —— chat 私有：任务列表快照 + 流中用量 + 产出文件（P1 新事件，csChat 不消费）——
        let todoItems = state.todoItems
        let streamUsage = state.streamUsage
        let fileOps = state.fileOps
        if (isCurrentSession) {
          if (evt.event === 'todo') {
            todoItems = evt.data.items
          } else if (evt.event === 'usage') {
            streamUsage = evt.data
          } else if (evt.event === 'file') {
            // 文件级去重：同一文件被后续步骤再次触达时以最新记录为准，
            // 且不影响同事件内其他文件（事件级去重会把它们一并吞掉）
            const incoming = evt.data.files.map((p) => ({
              path: p, node: evt.data.node, step_id: evt.data.step_id, ts: evt.data.ts,
            }))
            fileOps = [...fileOps.filter((f) => !incoming.some((i) => i.path === f.path)), ...incoming]
          }
        }

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

        // 公共字段（deltaText/currentStatus/nodeLabels）走共享归约
        const core = reduceStreamCore(state, evt)

        // 实时更新最后一条 assistant 消息的 streamEvents
        // - 终态：清空该字段（释放内存）
        // - 其他事件：环形追加（200 上限）
        const isTerminal = isTerminalEvent(evt)
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

        return { sessions, streamEvents: storeEvents, thinkingText, thinkingSeconds, thinkingStartAt, todoItems, streamUsage, fileOps, ...core }
      })
    },

    setCurrentRequestId: (id) => set({ currentRequestId: id }),

    resetStream: () => set({
      streamEvents: [], currentStatus: '', deltaText: '',
      thinkingText: '', thinkingSeconds: null, thinkingStartAt: 0,
      currentRequestId: null,
      todoItems: [], streamUsage: null, fileOps: [],
    }),

    // 重新生成：移除尾部 assistant 占位/旧回答（user 提问保留，问题由调用方重发）
    removeLastAssistant: (sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        return {
          sessions: state.sessions.map((s) => {
            if (s.id !== sid) return s
            const msgs = [...s.messages]
            if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') msgs.pop()
            return { ...s, messages: msgs, updatedAt: Date.now() }
          }),
        }
      })
    },

    replaceLastAssistant: (content, sessionId, sources, usage, thinking, thinkingSeconds) => {
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
              usage: usage || msgs[lastIdx].usage,
              thinking: thinking ?? msgs[lastIdx].thinking,
              thinkingSeconds: thinkingSeconds ?? msgs[lastIdx].thinkingSeconds,
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

        // 历史双写修复：旧数据每轮问答被前端+后端各存了一次，且中止轮次会留下空回答。
        // 恢复时过滤空气泡 + 对连续同角色同内容的消息去重，避免 UI 和上下文出现重复
        const restored: Message[] = []
        for (const m of msgs) {
          const content = typeof m.content === 'string' ? m.content : ''
          if (m.role === 'assistant' && !content.trim()) continue
          const prev = restored[restored.length - 1]
          if (prev && prev.role === m.role && prev.content === content) continue
          restored.push({
            id: nanoid(),
            role: m.role === 'assistant' ? 'assistant' : 'user',
            content,
            timestamp: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
          })
        }
        if (restored.length === 0) return

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

    // done 时固化执行过程快照到尾部 assistant 消息。
    // streamEvents 本体会被终态清空（防 OOM），这里在清空前由 useSSE 截快照传入
    attachTrace: (sessionId, trace) => {
      set((state) => ({
        sessions: state.sessions.map((s) => {
          if (s.id !== sessionId) return s
          const msgs = [...s.messages]
          const last = msgs.length - 1
          if (last >= 0 && msgs[last].role === 'assistant') {
            msgs[last] = { ...msgs[last], trace }
          }
          return { ...s, messages: msgs }
        }),
      }))
    },

    // —— 状态 ——
    setLoading: (v) => set({ isLoading: v }),
    setError: (e) => set({ error: e }),
    setHistoryError: (e) => set({ historyError: e }),
    bumpSessionsVersion: () => set((s) => ({ sessionsVersion: s.sessionsVersion + 1 })),
  }
})
