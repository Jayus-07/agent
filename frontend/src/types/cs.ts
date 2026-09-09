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
