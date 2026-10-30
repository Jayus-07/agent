// cs-workstation-v2 P1：用户端客服消息模型（全新组件树，不复用旧 csChat store）。

export type Cs2Role = 'user' | 'assistant' | 'agent' | 'system'

export interface Cs2Message {
  id: string
  role: Cs2Role
  content: string
  createdAt: number
  /** done 后固化的「本轮处理进度」节点标签 */
  csNodes?: string[]
  /** 流式/逐字揭示中 */
  streaming?: boolean
  /** 用户主动停止（REQUEST_CANCELLED） */
  interrupted?: boolean
  intentName?: string
  traceId?: string
}

export interface Cs2CandidateOption {
  id: string
  label: string
}

export interface Cs2ConversationDraft {
  /** 'new' = 欢迎态；否则为后端 conversation_id */
  id: string
  title: string
  status: string
  messages: Cs2Message[]
  /** 进行中的时间线节点（done 后固化到最后一条 assistant 气泡） */
  liveTimeline: string[]
  /** 状态栏当前节点 */
  liveStep: string | null
  streaming: boolean
  candidateOptions: Cs2CandidateOption[] | null
  clarificationQuestion: string | null
  rated: boolean
  /** done 事件携带的 pending_action 变更信号（P2 消费） */
  pendingActionType: string | null
  errorText: string | null
  pausedText: string | null
}

export function newConversationDraft(id = 'new'): Cs2ConversationDraft {
  return {
    id,
    title: '新的会话',
    status: 'active',
    messages: [],
    liveTimeline: [],
    liveStep: null,
    streaming: false,
    candidateOptions: null,
    clarificationQuestion: null,
    rated: false,
    pendingActionType: null,
    errorText: null,
    pausedText: null,
  }
}

/** SSE 主图节点名 → 原型时间线标签（后端子图节点转发后自动生效） */
const NODE_LABELS: Record<string, string> = {
  cs_graph_node: '客服图执行',
  cs_supervisor: '客服调度',
  cs_knowledge_expert: '知识检索',
  cs_query_expert: '业务查询',
  cs_action_expert: '业务操作',
  cs_complaint_expert: '投诉处理',
  cs_handoff_expert: '转人工',
  cs_pending_handler: '业务操作',
  cs_state_loader: '会话装载',
  cs_reporter: '客服汇总',
}

export function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? node
}
