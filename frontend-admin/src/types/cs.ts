export interface MessageDTO {
  message_id: string;
  sender_type: string;
  content: string;
  content_type: string;
  intent_domain: string | null;
  intent_name: string | null;
  confidence: number | null;
  trace_id: string | null;
  created_at: string;
}

export interface ConversationSummary {
  conversation_id: string;
  user_id: string;
  conversation_status: string;
  handling_mode: string;
  priority: string;
  message_count: number;
  trace_count: number;
  last_trace_id: string | null;
  last_activity_at: string | null;
  created_at: string;
  summary: string | null;
}

export interface ConversationDetail {
  conversation_id: string;
  user_id: string;
  conversation_status: string;
  handling_mode: string;
  priority: string;
  channel: string;
  summary: string | null;
  context_summary: string | null;
  trace_count: number;
  last_trace_id: string | null;
  created_at: string;
  last_activity_at: string | null;
  messages: MessageDTO[];
}

export interface PaginatedConversations {
  items: ConversationSummary[];
  total: number;
  has_more: boolean;
}

export interface ConversationTracesResponse {
  conversation_id: string;
  traces: import("./trace").TraceRecord[];
}

// ── 人工介入（坐席工作台）────────────────────────────
// 后端: backend/app/api/routes/cs_admin.py §人工介入（v1 轮询）

export interface HandoffQueueItem {
  conversation_id: string;
  user_id: string;
  handoff_state: string;
  trigger_type: string | null;
  trigger_reason: string | null;
  updated_at: string;
  last_message_preview: string | null;
}

export interface HandoffQueueResponse {
  items: HandoffQueueItem[];
  total: number;
}

export interface HandoffClaimResult {
  conversation_id: string;
  handoff_state: string;
  agent_id: string;
  already_claimed: boolean;
}

export interface HandoffMessageDTO {
  message_id: string;
  sender_type: string;
  content: string;
  content_type: string;
  created_at: string;
}

export interface HandoffMessagesResponse {
  conversation_id: string;
  handoff_state: string;
  last_id: number;
  messages: HandoffMessageDTO[];
}

// ── 满意度统计（014_cs_rating）────────────────────

export interface CSStatsResponse {
  session_count: number;
  message_count: number;
  rated_count: number;
  avg_rating: number | null;
  rating_dist: Record<string, number>;
  intent_dist: Array<{ name: string; count: number }>;
  handoff_count: number;
}
