// mock/traces/schemas/trace.ts — Trace 类型（= 未来 FastAPI Response）

import type { Span } from "./span";

// ═══════════════════════════════════
// 查询参数
// ═══════════════════════════════════
export interface TraceQuery {
  workflow_name?: string;
  status?: string;
  tags?: string[];
  user_id?: string;
  keyword?: string;
  start_time?: string;
  end_time?: string;
  sort_by?: "start_time" | "duration_ms" | "cost_usd" | "token_total";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

// ═══════════════════════════════════
// GET /api/traces 响应
// ═══════════════════════════════════
export interface TraceListResponse {
  items: TraceSummary[];
  total: number;
  page: number;
  page_size: number;
}

// ═══════════════════════════════════
// 列表
// ═══════════════════════════════════
export interface TraceSummary {
  trace_id: string;
  workflow_name: string;
  status: string;  // "success" | "error" | "running" | "cancelled" | "timeout"
  start_time: string;
  duration_ms: number;
  session_id?: string;       // 旧 fixture 可能缺失
  user_id?: string;
  user_name?: string;
  question: string;
  answer_preview: string;
  token_total: number;
  cost_usd: number;
  span_count: number;
  model_name: string;
  kb_id?: string;
  error_code: string | null;
  error_node?: string | null; // 失败 span id（用于错误面板跳转）
  parent_id?: string | null;
  children_ids?: string[];
  bookmarked?: boolean;
  sla_threshold_ms?: number;
  sla_breached?: boolean;
  tags?: string[];
}

// ═══════════════════════════════════
// 详情
// ═══════════════════════════════════
export interface TraceDetail {
  trace_id: string;
  workflow_name: string;

  request: {
    question: string;
    kb_id?: string;
    source?: string;
    params?: Record<string, unknown>;
    temperature?: number;
    max_tokens?: number;
  };

  response: {
    answer: string;
    answer_len: number;
  };

  usage: {
    total_tokens: number;
    total_cost_usd: number;
    llm_calls: number;
  };

  statistics: {
    total_spans: number;
    llm_latency_ms: number;
    retrieval_latency_ms: number;
    http_latency_ms: number;
    db_latency_ms: number;
  };

  error: {
    code: string;
    type: string;
    message: string;
    node: string | null;
    retry_count: number;
  } | null;

  /** LangGraph 图拓扑 */
  graph?: {
    nodes: { id: string; label: string }[];
    edges: { source: string; target: string; label?: string }[];
    max_loops: number;
    loop_count: number;
    degradation_triggered: boolean;
  };

  root_span_id: string;
  spans: Span[];
}
