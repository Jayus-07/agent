'use client'

// cs-workstation-v2 P4：assist.suggestion 事件的前端快照类型。
// 与后端 agent_assist.publish 的 payload 结构对齐（瞬态事件，契约只追加）。
export interface AssistSuggestion {
  kind: string
  source: string
  score: number | null
  text: string
}

export interface AssistIntent {
  name: string
  label: string
  confidence?: number
}

export interface AssistEmotion {
  label: string
  level: 'ok' | 'warn' | 'alert' | string
}

export interface AssistSopStep {
  label: string
  state: 'done' | 'cur' | 'todo' | string
}

export interface AssistSnapshot {
  conversationId: string
  trigger: 'claimed' | 'message' | 'followup' | string
  intent: AssistIntent | null
  emotion: AssistEmotion | null
  sop: AssistSopStep[]
  suggestions: AssistSuggestion[]
  at: number
}
