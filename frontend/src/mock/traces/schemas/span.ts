// mock/traces/schemas/span.ts — Span / Event 类型

// ── Span ──
export interface Span {
  trace_id: string;
  span_id: string;
  parent_id: string | null;

  name: string;
  type: SpanType;
  kind: SpanKind;

  status: "success" | "error" | "running" | "skipped";
  start_time: string;
  end_time: string;
  duration_ms: number;
  sequence: number;

  attributes: Record<string, unknown>;
  metrics: Record<string, number>;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  events: SpanEvent[];
  errors: string[];
}

// ── type ──
export type SpanType =
  | "workflow" | "agent"
  | "llm_call"
  | "embedding" | "retrieval" | "rerank"
  | "http" | "sql" | "email"
  | "database" | "transform"
  | "tool_call";

// ── kind ──
export type SpanKind =
  | "internal"        // 普通计算
  | "client"          // 外部请求
  | "server"          // 接收请求
  | "graph_node"      // LangGraph 图节点
  | "graph_route"     // 路由决策
  | "graph_loop"      // Supervisor 调度回合
  | "graph_fallback"; // 降级路径

// ── Event ──
export interface SpanEvent {
  name: string;
  timestamp: string;
  level: "debug" | "info" | "warn" | "error";
  message: string;
  attributes: Record<string, unknown>;
}
