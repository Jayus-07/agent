// ============================================================
// Trace / Span 数据模型（对齐后端 GET /rag-traces API）
// ============================================================

export interface TraceStep {
  id: string;
  label: string;
  duration_ms: number;
  duration_ratio: number;
  status: "success" | "skipped" | "error";
  metrics: Record<string, number | boolean | string>;
}

export interface TraceRecord {
  id: string;
  request_id: string;
  timestamp: string;
  session_id: string;
  question: string;
  answer_preview: string;
  answer_len: number;
  duration_ms: number;
  model: { name: string; provider: string };
  usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  cost: Record<string, unknown>;
  error: Record<string, unknown>;
  metadata: Record<string, unknown>;
  steps: TraceStep[];
}

export interface TraceListResponse {
  traces: TraceRecord[];
}

// 筛选器
export type TraceStatus = "all" | "success" | "error" | "timeout";

export interface TraceFilter {
  timeRange: "15m" | "1h" | "6h" | "24h" | "custom";
  status: TraceStatus;
  appName: string;
  keyword: string;
}

// 耗时颜色规则
export function durationColor(ms: number): string {
  if (ms > 5000) return "text-red-500";
  if (ms > 2000) return "text-amber-500";
  return "text-emerald-500";
}

export function durationBg(ms: number): string {
  if (ms > 5000) return "bg-red-50 border-red-200";
  if (ms > 2000) return "bg-amber-50 border-amber-200";
  return "";
}

export function statusDot(status: string): string {
  switch (status) {
    case "success": return "bg-emerald-500";
    case "error":   return "bg-red-500";
    case "timeout": return "bg-slate-400";
    default:        return "bg-slate-300";
  }
}

export function stepColor(status: string, ms: number): string {
  if (status === "skipped") return "bg-slate-200 border-dashed border-slate-300";
  if (status === "error")   return "bg-red-400";
  if (ms > 1000)            return "bg-amber-500";
  return "bg-violet-500";
}
