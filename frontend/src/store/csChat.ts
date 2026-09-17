import { create } from 'zustand'
import { nanoid } from 'nanoid'
import type { SSEStreamEvent } from '@/lib/types'
import { isTerminalEvent, reduceStreamCore } from '@/store/stream-reduce'
import type { CSConfirmationState, CSHandoffState } from '@/components/cs/constants'

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

  currentMessages: () => CSMessage[]
  newSession: () => string
  switchSession: (id: string) => void
  deleteSession: (id: string) => void

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

    // — SSE v2: 按事件类型分流更新 —
    //  公共字段（deltaText/currentStatus/nodeLabels）走 stream-reduce.ts 共享归约。
    //  流式期间不触碰 sessions：CS 无 message.streamEvents 消费方（时间线在终态
    //  由 replaceLastAssistant 从 csTimeline 一次性写入），每条 delta 重建
    //  messages 数组只会让整棵消息树白白重渲染。
    addStreamEvent: (evt, sessionId) => {
      set((state) => {
        const core = reduceStreamCore(state, evt)

        let currentNode = state.currentNode
        let intentDetected = state.intentDetected
        let csTimeline = state.csTimeline

        if (evt.event === 'status') {
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
