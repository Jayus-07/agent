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

/** 思考链增量（推理模型 reasoning_content，"已思考"折叠面板数据源） */
export interface ThinkingEvent {
  content: string
  ts: number
}

/** 本轮请求的 token 用量（后端 done 事件透出，来自 proxy per-turn 累加器） */
export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  /** 缓存命中 token（prompt 的子集，上游 API 支持时才有值） */
  cached_tokens?: number
  /** 推理 token（reasoning 模型才有值） */
  reasoning_tokens?: number
  /** 本轮 LLM 调用次数 */
  calls?: number
  /** 预估成本（USD） */
  cost_usd?: number
  /** 按模型细分 */
  models?: Record<string, { prompt_tokens: number; completion_tokens: number; total_tokens: number; calls: number }>
}

export interface DoneEvent {
  elapsed: number
  sources?: Source[]
  usage?: TokenUsage
}

export interface ErrorEvent {
  message: string
  ts: number
}

// ========================================
// P1: todo 快照 + 流中用量
// ========================================

/** 任务列表项（planner plan 的前端视图） */
export interface TodoItem {
  id: string
  text: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed' | 'skipped'
}

export interface TodoEvent {
  items: TodoItem[]
  ts: number
}

/** 流中用量（supervisor 每轮调度后透出的轮内累计） */
export interface UsageEvent extends TokenUsage {
  ts: number
}

/** 工具落盘文件事件（export_csv 等工具的产出清单） */
export interface FileEvent {
  node: string
  step_id: string
  files: string[]
  ts: number
}

/** SSE v2 事件联合类型 */
export type SSEStreamEvent =
  | { event: 'meta';     data: MetaEvent }
  | { event: 'status';   data: StatusEvent }
  | { event: 'log';      data: LogEvent }
  | { event: 'delta';    data: DeltaEvent }
  | { event: 'thinking'; data: ThinkingEvent }
  | { event: 'todo';     data: TodoEvent }
  | { event: 'usage';    data: UsageEvent }
  | { event: 'file';     data: FileEvent }
  | { event: 'done';     data: DoneEvent }
  | { event: 'error';    data: ErrorEvent }

// ========================================
// 对话模式
// ========================================

export type ChatMode = 'chat' | 'sql' | 'rag' | 'report'

// ========================================
// 消息
// ========================================

/** done 时固化的执行过程快照（完成态常驻行 CompletionLine 回看用） */
export interface AgentTrace {
  /** 本轮总耗时（秒，done 事件 elapsed） */
  elapsed: number
  streamEvents: SSEStreamEvent[]
  todoItems: TodoItem[]
  nodeLabels: Record<string, string>
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  /** 执行过程快照（done 时写入；历史恢复的消息无此字段） */
  trace?: AgentTrace
  /** SSE v2 流式事件 */
  streamEvents?: SSEStreamEvent[]
  /** 来源文档（仅 RAG 类问题有值） */
  sources?: Source[]
  /** 本轮请求 token 用量（done 事件写入；streamEvents 终态会被清空，用量需单独持久化） */
  usage?: TokenUsage
  /** 思考链全文（done 事件时从 store.thinkingText 固化；后端未下发思考链则无值） */
  thinking?: string
  /** 思考耗时（秒，前端从首条 thinking 到首条 delta 计时；未走完思考链则无值） */
  thinkingSeconds?: number
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
