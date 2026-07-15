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
  /** 可选的输入输出（P1 后端支持后启用） */
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  /** 子步骤（展开后有细粒度信息） */
  children?: TraceStep[];
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
  /** 请求级别状态 */
  status?: "success" | "error" | "timeout" | "cancelled";
}

export interface TraceListResponse {
  traces: TraceRecord[];
  total: number;
  stats?: TraceStats;
}

export interface TraceStats {
  total_24h: number;
  success_rate: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
  error_count: number;
}

// 筛选器
export type TraceStatus = "all" | "success" | "error" | "timeout" | "cancelled";
export type SortField = "duration_ms" | "timestamp" | "usage.total_tokens" | "";
export type SortDir = "asc" | "desc";

export interface TraceFilter {
  timeRange: "15m" | "1h" | "6h" | "24h" | "custom";
  status: TraceStatus;
  keyword: string;
  sortField: SortField;
  sortDir: SortDir;
  page: number;
  pageSize: number;
}

// ============================================================
// 展示工具函数
// ============================================================

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
    case "success":   return "bg-emerald-500";
    case "error":     return "bg-red-500";
    case "timeout":   return "bg-amber-500";
    case "cancelled": return "bg-slate-400";
    default:          return "bg-slate-300";
  }
}

export function statusBadge(status: string): { bg: string; text: string; label: string } {
  switch (status) {
    case "success":   return { bg: "bg-emerald-100 text-emerald-700", text: "text-emerald-700", label: "SUCCESS" };
    case "error":     return { bg: "bg-red-100 text-red-700", text: "text-red-700", label: "ERROR" };
    case "timeout":   return { bg: "bg-amber-100 text-amber-700", text: "text-amber-700", label: "TIMEOUT" };
    case "cancelled": return { bg: "bg-slate-100 text-slate-500", text: "text-slate-500", label: "CANCELLED" };
    case "skipped":   return { bg: "bg-slate-100 text-slate-500", text: "text-slate-500", label: "SKIPPED" };
    default:          return { bg: "bg-slate-100 text-slate-500", text: "text-slate-500", label: status.toUpperCase() };
  }
}

export function stepColor(status: string, ms: number): string {
  if (status === "skipped" || status === "cancelled") return "bg-slate-200 border-dashed border-slate-300";
  if (status === "error") return "bg-red-400";
  if (ms > 1000) return "bg-amber-500";
  return "bg-violet-500";
}

export function formatTime(iso: string): string {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export function truncate(s: string, n: number): string {
  if (!s) return "--";
  return s.length > n ? s.slice(0, n) + "..." : s;
}
