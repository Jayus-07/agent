// ========================================
// API 请求 / 响应类型
// ========================================

export interface ChatRequest {
  question: string
  session_id: string
}

export interface ChatResponse {
  answer: string
  session_id: string
}

// ========================================
// SSE 流式事件 (对应后端 SSEEvent)
// ========================================

export type SSEStage =
  | 'planning'
  | 'supervising'
  | 'executing'
  | 'reporting'
  | 'done'
  | 'error'

export interface SSEEvent {
  stage: SSEStage
  label: string
  message: string
  node: string
  data: {
    task_count?: number
    tasks?: string[]
    ready?: string[]
    completed?: string[]
    all_done?: boolean
    success_count?: number
    step_id?: string
    status?: 'running' | 'success' | 'failed' | 'skipped'
    description?: string
    error?: string
    preview?: string
    final_answer?: string
    elapsed?: number
    started_at?: number
    finished_at?: number
  }
}

// ========================================
// 对话模式
// ========================================

export type ChatMode = 'chat' | 'sql' | 'rag' | 'report'

// ========================================
// 消息
// ========================================

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  /** SSE 思考过程事件（仅 assistant + stream 模式有值） */
  thinking?: SSEEvent[]
}

// ========================================
// 会话
// ========================================

export interface Session {
  id: string
  title: string
  mode: ChatMode
  messages: Message[]
  createdAt: number
  updatedAt: number
}
