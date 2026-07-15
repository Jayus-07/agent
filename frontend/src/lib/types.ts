// ========================================
// API 请求类型
// ========================================

export interface ChatRequest {
  question: string
  session_id: string
  kb_id?: string
  request_id?: string   // 用于中止信号路由
}

// ========================================
// 来源文档
// ========================================

export interface Source {
  filename: string
  doc_type: string
  type_label: string
  score: number | null
}

// ========================================
// SSE v2 流式事件（event: meta|status|log|delta|done|error）
// ========================================

export interface MetaEvent {
  node_labels: Record<string, string>   // node → emoji 映射
}

export interface StatusEvent {
  node: string    // LangGraph 节点名（前端自行映射为 emoji 标签）
  ts: number
}

export interface LogEvent {
  level: 'info' | 'warn' | 'error'
  node: string
  step_id: string
  message: string
  payload: Record<string, unknown>   // Worker 入参/出参详情
  ts: number
}

export interface DeltaEvent {
  content: string   // 句子块
  ts: number
}

export interface DoneEvent {
  elapsed: number
  sources?: Source[]
}

export interface ErrorEvent {
  message: string
  ts: number
}

/** SSE v2 事件联合类型 */
export type SSEStreamEvent =
  | { event: 'meta';   data: MetaEvent }
  | { event: 'status'; data: StatusEvent }
  | { event: 'log';    data: LogEvent }
  | { event: 'delta';  data: DeltaEvent }
  | { event: 'done';   data: DoneEvent }
  | { event: 'error';  data: ErrorEvent }

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
  /** SSE v2 流式事件 */
  streamEvents?: SSEStreamEvent[]
  /** 来源文档（仅 RAG 类问题有值） */
  sources?: Source[]
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
