import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { SSEStreamEvent } from '@/lib/types'
import type { CSConfirmationState, CSHandoffState } from '@/components/cs/constants'

export interface CSMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  streamEvents?: SSEStreamEvent[]
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
  streamEvents: SSEStreamEvent[]
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

  currentMessages: () => CSMessage[]
  newSession: () => string
  switchSession: (id: string) => void
  deleteSession: (id: string) => void

  addMessage: (role: 'user' | 'assistant', content: string, sessionId?: string) => void
  addStreamEvent: (evt: SSEStreamEvent, sessionId?: string) => void
  replaceLastAssistant: (content: string, sessionId?: string) => void

  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  setCurrentRequestId: (id: string | null) => void
  resetStream: () => void
  setIntentDetected: (intent: string | null) => void
  setConfirmationState: (state: CSConfirmationState) => void
  setHandoffState: (state: CSHandoffState) => void
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

export const useCSChatStore = create<CSChatState>((set, get) => {
  const initialSession = createCSSession()
  return {
    sessions: [initialSession],
    currentId: initialSession.id,
    streamEvents: [],
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
      }))
      return s.id
    },

    switchSession: (id) => set({ currentId: id, error: null }),

    deleteSession: (id) => {
      set((state) => {
        const remaining = state.sessions.filter((s) => s.id !== id)
        if (remaining.length === 0) {
          const fallback = createCSSession()
          return { sessions: [fallback], currentId: fallback.id }
        }
        return {
          sessions: remaining,
          currentId: state.currentId === id ? remaining[0].id : state.currentId,
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

    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        const sid = targetId(state, sessionId)
        const isCurrentSession = !sessionId || sessionId === state.currentId
        const MAX_EVENTS = 200

        let storeEvents = state.streamEvents
        if (isCurrentSession) {
          if (evt.event === 'done') {
            storeEvents = []
          } else if (evt.event !== 'meta') {
            storeEvents = storeEvents.length >= MAX_EVENTS
              ? [...storeEvents.slice(storeEvents.length - MAX_EVENTS + 1), evt]
              : [...storeEvents, evt]
          }
        }

        let deltaText = state.deltaText
        let currentStatus = state.currentStatus
        let nodeLabels = state.nodeLabels
        let intentDetected = state.intentDetected
        let currentNode = state.currentNode
        let csTimeline = state.csTimeline
        const isTerminal = evt.event === 'done' || evt.event === 'error'

        if (evt.event === 'meta') {
          nodeLabels = evt.data.node_labels
        } else if (evt.event === 'status') {
          currentStatus = evt.data.node
          currentNode = evt.data.node
          if (evt.data.node.startsWith('cs_')) {
            csTimeline = [...csTimeline, evt.data.node]
            if (!intentDetected && evt.data.node !== 'cs_knowledge') {
              const intentMap: Record<string, string> = {
                cs_business_query: '业务查询',
                cs_business_action: '业务办理',
                cs_complaint: '投诉处理',
                cs_handoff: '人工转接',
              }
              intentDetected = intentMap[evt.data.node] ?? null
            }
          }
        } else if (evt.event === 'delta') {
          deltaText = state.deltaText + evt.data.content
        } else if (isTerminal) {
          currentStatus = ''
          currentNode = null
        }

        const sessions = state.sessions.map((s) => {
          if (s.id !== sid) return s
          const msgs = s.messages.map((m, idx) => {
            if (idx !== s.messages.length - 1 || m.role !== 'assistant') return m
            if (isTerminal) {
              return { ...m, streamEvents: [], csNodes: csTimeline }
            }
            const cur = m.streamEvents || []
            const next = cur.length >= MAX_EVENTS
              ? [...cur.slice(cur.length - MAX_EVENTS + 1), evt]
              : [...cur, evt]
            return { ...m, streamEvents: next }
          })
          return { ...s, messages: msgs, updatedAt: Date.now() }
        })

        return {
          sessions, streamEvents: storeEvents, deltaText, currentStatus,
          nodeLabels, intentDetected, currentNode, csTimeline,
        }
      })
    },

    setCurrentRequestId: (id) => set({ currentRequestId: id }),

    resetStream: () => set({
      streamEvents: [], currentStatus: '', deltaText: '',
      currentRequestId: null, currentNode: null, csTimeline: [],
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
  }
})
