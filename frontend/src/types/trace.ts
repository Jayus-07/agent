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
  /** P1: LLM 调用明细（prompt / response / model / temperature） */
  llm_call?: {
    model: string;
    temperature: number;
    prompt_text: string;
    response_text: string;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
  /** 网络/客户端耗时拆分 */
  http_breakdown?: {
    dns_ms: number;
    connect_ms: number;
    tls_ms: number;
    ttfb_ms: number;
    body_ms: number;
  };
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
  /** P0: 成本（USD），与 cost 字段并存做兼容 */
  cost_usd?: number;
  error: Record<string, unknown>;
  metadata: Record<string, unknown>;
  steps: TraceStep[];
  /** 请求级别状态 */
  status?: "success" | "error" | "timeout" | "cancelled";
  /** P0: trace 关联（parent / children） */
  parent_id?: string | null;
  children_ids?: string[];
  /** P0: Session 聚合用字段 */
  session?: {
    user_id?: string;
    user_name?: string;
    started_at: string;
    trace_count: number;
  };
  /** P1: SLA 阈值 */
  sla?: {
    threshold_ms: number;
    breached: boolean;
  };
  /** P1: 是否收藏 */
  bookmarked?: boolean;
  /** P0: 24h 成功率/P95 sparkline 趋势 */
  sparkline?: {
    success_rate: number[];
    p95_ms: number[];
  };
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
  /** P0: 总成本 */
  total_cost_usd?: number;
  /** P1: 告警聚合 */
  alerts?: AlertItem[];
}

export interface AlertItem {
  id: string;
  severity: "warning" | "error" | "critical";
  type: "sla_breach" | "error_rate" | "cost_anomaly" | "high_latency";
  message: string;
  trace_ids: string[];
  created_at: string;
  resolved?: boolean;
}

// 筛选器
export type TraceStatus = "all" | "success" | "error" | "timeout" | "cancelled";
export type SortField = "duration_ms" | "timestamp" | "usage.total_tokens" | "cost_usd" | "";
export type SortDir = "asc" | "desc";

export interface TraceFilter {
  timeRange: "15m" | "1h" | "6h" | "24h" | "custom";
  status: TraceStatus;
  keyword: string;
  sortField: SortField;
  sortDir: SortDir;
  page: number;
  pageSize: number;
  /** P2: 可选 KB 筛选 */
  kb_id?: string;
  /** P2: 可选模型筛选 */
  model?: string;
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

/** P2: 相对时间显示（如 "3 分钟前"） */
export function formatRelative(iso: string, now: Date = new Date()): string {
  if (!iso) return "--";
  const d = new Date(iso);
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/** P2: 同时显示相对 + 绝对时间 */
export function formatBoth(iso: string): string {
  return `${formatRelative(iso)} · ${formatTime(iso)}`;
}

export function truncate(s: string, n: number): string {
  if (!s) return "--";
  return s.length > n ? s.slice(0, n) + "..." : s;
}

/** P0: 成本格式化（USD） */
export function formatCost(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "--";
  if (usd === 0) return "$0.00";
  if (usd < 0.0001) return `$${(usd * 1_000_000).toFixed(2)}µ`;
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/** P0: 把 trace list 按时间范围真实过滤 */
export function filterByTimeRange<T extends { timestamp: string }>(
  traces: T[],
  range: "15m" | "1h" | "6h" | "24h" | "custom"
): T[] {
  const now = Date.now();
  const ranges: Record<string, number> = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
  };
  const ms = ranges[range];
  if (ms === undefined) return traces; // "custom" 或未知范围：不过滤
  return traces.filter((t) => {
    const ts = new Date(t.timestamp).getTime();
    return now - ts <= ms;
  });
}

/** P1: 告警严重度样式 */
export function severityStyle(sev: AlertItem["severity"]): { bg: string; text: string; label: string } {
  switch (sev) {
    case "critical": return { bg: "bg-red-100 text-red-700", text: "text-red-700", label: "CRITICAL" };
    case "error":    return { bg: "bg-orange-100 text-orange-700", text: "text-orange-700", label: "ERROR" };
    case "warning":  return { bg: "bg-amber-100 text-amber-700", text: "text-amber-700", label: "WARNING" };
  }
}