import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { SSEStreamEvent } from '@/lib/types'
import { isTerminalEvent, reduceStreamCore } from '@/store/stream-reduce'
import type { CSConfirmationState, CSHandoffState } from '@/components/cs/constants'
import type { MyConversationItem, PendingActionSnapshot } from '@/api/cs'
import type { PendingActionInfo } from '@/lib/types'

export interface CSMessage {
  id: string
  // 2026-09-17: 新增 'agent' —— 人工坐席消息（useCSHandoffSync 轮询落库）
  role: 'user' | 'assistant' | 'agent'
  content: string
  timestamp: number
  csNodes?: string[]
}

export interface CSSession {
  id: string
  title: string
  messages: CSMessage[]
  createdAt: number
  updatedAt: number
}

interface CSChatState {
  sessions: CSSession[]
  currentId: string
  currentStatus: string
  deltaText: string
  nodeLabels: Record<string, string>
  isLoading: boolean
  error: string | null
  currentRequestId: string | null
  intentDetected: string | null
  confirmationState: CSConfirmationState
  handoffState: CSHandoffState
  currentNode: string | null
  csTimeline: string[]
  /** 每个客服会话独立保存权威待确认快照；刷新后由服务端恢复。 */
  pendingBySession: Record<string, PendingActionSnapshot | null>
  // 退款候选点选（2026-10-08）：后端 clarification 帧（source=refund_candidates）
  candidateOptions: { id: string; label: string }[] | null
  // 转人工等待元信息（A 案排队透明化）：倒计时数据源
  handoffMeta: { handoff_id: string; handoff_state: string; total_deadline_at: string | null; queue_position: number } | null
  /** 双向输入中指示：坐席正在输入（/my/messages 轮询携带，TTL 5s） */
  agentTyping: boolean

  currentMessages: () => CSMessage[]
  newSession: () => string
  switchSession: (id: string) => void
  deleteSession: (id: string) => void
  hydrateFromServer: (items: MyConversationItem[]) => void

  addMessage: (role: 'user' | 'assistant' | 'agent', content: string, sessionId?: string) => void
  addStreamEvent: (evt: SSEStreamEvent, sessionId?: string) => void
  replaceLastAssistant: (content: string, sessionId?: string) => void

  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setCurrentRequestId: (id: string | null) => void
  resetStream: () => void
  setIntentDetected: (intent: string | null) => void
  setConfirmationState: (state: CSConfirmationState) => void
  setHandoffState: (state: CSHandoffState) => void
  setPendingAction: (sessionId: string, pending: PendingActionSnapshot | null) => void
  setCandidateOptions: (opts: { id: string; label: string }[] | null) => void
  setHandoffMeta: (m: { handoff_id: string; handoff_state: string; total_deadline_at: string | null; queue_position: number } | null) => void
  setAgentTyping: (v: boolean) => void
}

function createCSSession(): CSSession {
  const id = nanoid()
  return {
    id,
    title: '新客服会话',
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  }
}

function targetId(state: CSChatState, sid?: string): string {
  return sid ?? state.currentId
}

/** 服务端消息 → 前端气泡。id 用 message_id（与 useCSHandoffSync 轮询幂等去重共用） */
function mapServerMessage(m: MyConversationItem['messages'][number]): CSMessage | null {
  if (!m.content) return null
  const role: CSMessage['role'] =
    m.sender_type === 'human_agent'
      ? 'agent'
      : m.sender_type === 'user'
        ? 'user'
        : 'assistant'
  return {
    id: m.message_id,
    role,
    content: m.content,
    timestamp: m.created_at ? Date.parse(m.created_at) || Date.now() : Date.now(),
    csNodes: [],
  }
}

function mapConversation(c: MyConversationItem): CSSession | null {
  const messages = c.messages
    .map(mapServerMessage)
    .filter((m): m is CSMessage => m !== null)
  if (messages.length === 0) return null
  const firstUser = c.messages.find((m) => m.sender_type === 'user')?.content ?? ''
  const title = c.summary?.trim()
    || (firstUser ? firstUser.slice(0, 30) + (firstUser.length > 30 ? '...' : '') : '客服会话')
  return {
    id: c.conversation_id,
    title,
    messages,
    createdAt: c.created_at ? Date.parse(c.created_at) || Date.now() : Date.now(),
    updatedAt: c.last_activity_at
      ? Date.parse(c.last_activity_at) || Date.now()
      : Date.now(),
  }
}

export const useCSChatStore = create<CSChatState>((set, get) => {
  const initialSession = createCSSession()
  return {
    sessions: [initialSession],
    currentId: initialSession.id,
    currentStatus: '',
    deltaText: '',
    nodeLabels: {},
    isLoading: false,
    error: null,
    currentRequestId: null,
    intentDetected: null,
    confirmationState: 'none',
    handoffState: 'none',
    currentNode: null,
    csTimeline: [],
    pendingBySession: {},
    candidateOptions: null,
    handoffMeta: null,
    agentTyping: false,

    currentMessages: () => {
      const s = get().sessions.find((s) => s.id === get().currentId)
      return s?.messages ?? []
    },

    newSession: () => {
      const s = createCSSession()
      set((state) => ({
        sessions: [s, ...state.sessions],
        currentId: s.id,
        error: null,
        intentDetected: null,
        confirmationState: 'none',
        handoffState: 'none',
        currentNode: null,
        csTimeline: [],
        agentTyping: false,
      }))
      return s.id
    },

    switchSession: (id) => set({ currentId: id, error: null }),

    // 刷新后恢复：抽屉打开时用服务端「我的客服会话」水合。
    // 服务端按 last_activity_at 倒序返回；只并入本地没有的会话（幂等）。
    // STOP CS-A P0-3/F5：关抽屉/刷新窗口期间完成的轮次也要补齐 ——
    // 当前会话若在服务端有记录且本地未在流式中，以服务端消息为准补齐
    // （仅当服务端条数 ≥ 本地时替换：record_cs_turn 是 fire-and-forget，
    // 服务端短暂滞后时绝不回退本地已显示的内容）。
    hydrateFromServer: (items) => {
      const restored = items
        .map(mapConversation)
        .filter((s): s is CSSession => s !== null)
      if (restored.length === 0) return
      set((state) => {
        let sessions = state.sessions
        // F5：当前会话服务端补齐（不在流式中才允许，流式中本地是权威）
        const serverCurrent = restored.find((r) => r.id === state.currentId)
        if (serverCurrent && !state.isLoading) {
          sessions = sessions.map((s) => {
            if (s.id !== state.currentId) return s
            const serverLonger = serverCurrent.messages.length >= s.messages.length
            const sameAsServer =
              s.messages.length === serverCurrent.messages.length &&
              s.messages.every((m, i) => serverCurrent.messages[i]?.id === m.id)
            if (sameAsServer || !serverLonger) return s
            return {
              ...s,
              messages: serverCurrent.messages,
              updatedAt: Math.max(s.updatedAt, serverCurrent.updatedAt),
            }
          })
        }
        const existingIds = new Set(sessions.map((s) => s.id))
        const fresh = restored.filter((s) => !existingIds.has(s.id))
        if (fresh.length === 0 && sessions === state.sessions) return {}
        const current = sessions.find((s) => s.id === state.currentId)
        const currentActive = !!current && current.messages.length > 0
        if (currentActive || fresh.length === 0) {
          // 用户本轮已在聊：只并入历史，不抢 currentId
          return fresh.length > 0 ? { sessions: [...fresh, ...sessions] } : { sessions }
        }
        // 当前是未动过的空占位 → 最近一条恢复为当前会话，空占位丢弃
        const latest = fresh[0]
        const others = sessions.filter((s) => s.messages.length > 0)
        return {
          sessions: [
            latest,
            ...fresh.filter((s) => s.id !== latest.id),
            ...others,
          ],
          currentId: latest.id,
        }
      })
    },

    deleteSession: (id) => {
      set((state) => {
        const remaining = state.sessions.filter((s) => s.id !== id)
        const pendingBySession = { ...state.pendingBySession }
        delete pendingBySession[id]
        if (remaining.length === 0) {
          const fallback = createCSSession()
          return { sessions: [fallback], currentId: fallback.id, pendingBySession }
        }
        return {
          sessions: remaining,
          currentId: state.currentId === id ? remaining[0].id : state.currentId,
          pendingBySession,
        }
      })
    },

    addMessage: (role, content, sessionId) => {
      const msg: CSMessage = {
        id: nanoid(),
        role,
        content,
        timestamp: Date.now(),
        csNodes: [],
      }
      set((state) => {
        const sid = targetId(state, sessionId)
        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const title = s.messages.length === 0 && role === 'user'
            ? content.slice(0, 30) + (content.length > 30 ? '...' : '')
            : s.title
          return { ...s, title, messages: [...s.messages, msg], updatedAt: Date.now() }
        })
        return { sessions }
      })
    },

    // — SSE v2: 按事件类型分流更新 —
    //  公共字段（deltaText/currentStatus/nodeLabels）走 stream-reduce.ts 共享归约。
    //  流式期间不触碰 sessions：CS 无 message.streamEvents 消费方（时间线在终态
    //  由 replaceLastAssistant 从 csTimeline 一次性写入），每条 delta 重建
    //  messages 数组只会让整棵消息树白白重渲染。
    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        // #7 请求归属：非当前会话的流事件直接丢弃——切换会话不 abort 旧流时，
        // 旧流的 delta/status 会写进新会话的共享字段（内容互串）。
        // 空对象 = 不更新任何字段。
        if (sessionId && sessionId !== state.currentId) return {}

        const core = reduceStreamCore(state, evt)

        let currentNode = state.currentNode
        let intentDetected = state.intentDetected
        let csTimeline = state.csTimeline

        if (evt.event === 'status') {
          currentNode = evt.data.node
          if (evt.data.node.startsWith('cs_')) {
            csTimeline = [...csTimeline, evt.data.node]
            // 键 = 后端真实节点名（backend/customer_service/graph_state.py 的 `CS_*` 常量）。
            // 刻意跳过知识专家（它不是「业务意图」）。
            // ⚠️ 当前 SSE 只下发主图节点名（cs_graph_node），子图内部节点未转发，
            // 故 intentDetected 实际恒为 null——待后端转发子图事件后自动生效。
            if (!intentDetected && evt.data.node !== 'cs_knowledge_expert') {
              const intentMap: Record<string, string> = {
                cs_query_expert: '业务查询',
                cs_action_expert: '业务办理',
                cs_complaint_expert: '投诉处理',
                cs_handoff_expert: '人工转接',
              }
              intentDetected = intentMap[evt.data.node] ?? null
            }
          }
        } else if (isTerminalEvent(evt)) {
          currentNode = null
        }

        return { ...core, intentDetected, currentNode, csTimeline }
      })
    },

    setCurrentRequestId: (id) => set({ currentRequestId: id }),

    resetStream: () => set({
      currentStatus: '', deltaText: '',
      currentRequestId: null, currentNode: null, csTimeline: [],
      candidateOptions: null, handoffMeta: null,
    }),

    replaceLastAssistant: (content, sessionId) => {
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
              timestamp: Date.now(),
              csNodes: state.csTimeline,
            }
          }
          return { ...s, messages: msgs, updatedAt: Date.now() }
        }),
      }))
    },

    setLoading: (v) => set({ isLoading: v }),
    setError: (e) => set({ error: e }),
    setIntentDetected: (intent) => set({ intentDetected: intent }),
    setConfirmationState: (state) => set({ confirmationState: state }),
    setHandoffState: (state) => set({ handoffState: state }),
    setPendingAction: (sessionId, pending) => set((state) => ({
      pendingBySession: { ...state.pendingBySession, [sessionId]: pending },
    })),
    setCandidateOptions: (opts) => set({ candidateOptions: opts }),
    setHandoffMeta: (m) => set({ handoffMeta: m }),
    setAgentTyping: (v) => set({ agentTyping: v }),
  }
})
