// ============================================================
// Trace / Span 数据模型（通用 Workflow Trace 平台）
// 设计原则：Trace 不识别任何业务，只认知 Trace → Span → Event 三层
// ============================================================

// ── Mock 数据系统类型（Summary/Detail 分离） ──────────

/** 列表页 Summary（轻量，~22 字段） */
export interface TraceSummary {
  trace_id: string;
  workflow_name: string;
  status: "success" | "error" | "running" | "cancelled" | "timeout";
  start_time: string;
  duration_ms: number;
  session_id: string;
  user_id: string;
  user_name: string;
  question: string;
  answer_preview: string;
  token_total: number;
  cost_usd: number;
  span_count: number;
  model_name: string;
  kb_id: string;
  sla_threshold_ms: number;
  sla_breached: boolean;
  error_code: string | null;
  error_node: string | null;
  parent_id: string | null;
  children_ids: string[];
  bookmarked: boolean;
}

/** 详情页 Detail（重数据：request/response/usage/error/spans） */
export interface TraceDetail {
  trace_id: string;
  request: {
    question: string;
    kb_id?: string;
    temperature?: number;
    max_tokens?: number;
  };
  response: {
    answer: string;
    answer_len: number;
  };
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
    model_name: string;
    model_provider: string;
  };
  error: {
    code: string;
    message: string;
    node: string | null;
    retry_count: number;
  } | null;
  root_span_id: string;
  spans: Span[];
}

// ── Event ────────────────────────────────────────────

export interface SpanEvent {
  ts: string;
  name: string;
  /** 人类可读描述（Timeline 渲染用） */
  message?: string;
  /** 事件级别 */
  level?: "debug" | "info" | "warn" | "error";
  data: Record<string, unknown>;
}

// ── Span（通用执行节点） ─────────────────────────────

export interface Span {
  id: string;                         // span 唯一 ID
  type: string;                       // 节点类型：agent | llm_call | retrieval | rerank | tool_call | http | sql | memory | workflow | custom
  name: string;                       // 人类可读名称（如 "LLM生成"、"混合检索"）
  parent_id: string | null;           // 父 span ID（null = 根 span）
  status: "running" | "success" | "error" | "skipped";
  start_time: string;                 // ISO 8601
  end_time?: string;                  // ISO 8601
  duration_ms: number;
  duration_ratio: number;             // 占总 trace 耗时比例 (0-1)
  /** 通用属性（OpenTelemetry 风格：llm.model, retrieval.method 等） */
  attributes: Record<string, unknown>;
  /** 数值指标（token、文档数、延迟等） */
  metrics: Record<string, number | boolean | string>;
  /** 输入快照 */
  input?: Record<string, unknown>;
  /** 输出快照 */
  output?: Record<string, unknown>;
  /** LLM 调用明细（type=llm_call 时有值） */
  llm_call?: {
    model: string;
    temperature: number;
    prompt_text: string;
    response_text: string;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
  };
  /** 网络耗时拆分（type=http 或含 HTTP 调用时有值） */
  http_breakdown?: {
    dns_ms: number;
    connect_ms: number;
    tls_ms: number;
    ttfb_ms: number;
    body_ms: number;
  };
  /** 子 span id 列表（加速前端遍历） */
  children: string[];
  /** Span 内事件（LLM Streaming、Retry、Tool Call 等） */
  events: SpanEvent[];
  /** 非致命告警 */
  warnings: string[];
  /** 致命错误 */
  errors: string[];
}

// ── TraceRecord（一次完整工作流执行） ────────────────

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
  usage: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; cost_usd?: number };
  cost: Record<string, unknown>;
  cost_usd?: number;
  error: Record<string, unknown>;
  metadata: Record<string, unknown>;
  /** ✅ 新模型：通用 Span 树（替代旧的 steps 扁平数组） */
  spans: Span[];
  /** 工作流名称（如 "rag_agent", "multi_agent_compare"） */
  workflow_name?: string;
  /** 工作流版本 */
  workflow_version?: number;
  /** 根 span ID */
  root_span_id?: string;
  status?: "success" | "error" | "timeout" | "cancelled" | "running";
  parent_id?: string | null;
  children_ids?: string[];
  session?: {
    user_id?: string;
    user_name?: string;
    started_at: string;
    trace_count: number;
  };
  /** LangGraph 图拓扑（agent workflow 才有） */
  graph?: {
    nodes: { id: string; label: string }[];
    edges: { source: string; target: string; label?: string }[];
    max_loops: number;
    loop_count: number;
    degradation_triggered: boolean;
  };
  sla?: {
    threshold_ms: number;
    breached: boolean;
  };
  bookmarked?: boolean;
  sparkline?: {
    success_rate: number[];
    p95_ms: number[];
  };
}

// ── 向后兼容 ─────────────────────────────────────────
// 旧代码中仍有 TraceStep 引用的过渡期类型别名

/** @deprecated 使用 Span 替代。保留用于渐进迁移。 */
export interface TraceStep {
  id: string;
  label: string;
  duration_ms: number;
  duration_ratio: number;
  status: "success" | "skipped" | "error";
  metrics: Record<string, number | boolean | string>;
  llm_call?: Span["llm_call"];
  http_breakdown?: Span["http_breakdown"];
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  children?: TraceStep[];
  /** Span type（新增） */
  type?: string;
  /** Span name（新增，映射到 label） */
  name?: string;
}

// ── API 响应 ─────────────────────────────────────────

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
  total_cost_usd?: number;
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
  kb_id?: string;
  model?: string;
}

// ── Span type 枚举 ───────────────────────────────────
// 前端按 type 路由渲染，新增 type 只需加一个 case

export const SPAN_TYPES = [
  "agent",
  "llm_call",
  "retrieval",
  "rerank",
  "tool_call",
  "http",
  "sql",
  "memory",
  "workflow",
  "custom",
] as const;
export type SpanType = (typeof SPAN_TYPES)[number];

/** Span type → 中文标签映射 */
export const SPAN_TYPE_LABELS: Record<string, string> = {
  agent: "Agent",
  llm_call: "LLM 调用",
  retrieval: "检索",
  rerank: "Rerank",
  tool_call: "工具调用",
  http: "HTTP 请求",
  sql: "SQL 查询",
  memory: "记忆",
  workflow: "工作流",
  custom: "自定义",
};

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

/** Span 状态颜色（替代 stepColor，接受 Span 或兼容 status+ms） */
export function spanColor(status: string, ms: number): string {
  if (status === "skipped" || status === "cancelled") return "bg-slate-200 border-dashed border-slate-300";
  if (status === "error") return "bg-red-400";
  if (ms > 1000) return "bg-amber-500";
  return "bg-violet-500";
}

/** @deprecated 使用 spanColor 替代 */
export const stepColor = spanColor;

/** Span type → 颜色映射（用于图中区分节点类型） */
export function spanTypeColor(type: string): string {
  switch (type) {
    case "agent":     return "bg-indigo-500";
    case "llm_call":  return "bg-violet-500";
    case "retrieval": return "bg-emerald-500";
    case "rerank":    return "bg-amber-500";
    case "tool_call": return "bg-cyan-500";
    case "http":      return "bg-blue-500";
    case "sql":       return "bg-orange-500";
    case "memory":    return "bg-pink-500";
    case "workflow":  return "bg-teal-500";
    default:          return "bg-slate-400";
  }
}

/** 安全数字转换：unknown → string。
 *  - undefined / null / 非数字字符串 → fallback（默认 "--"）
 *  - 0（合法值）/ 数字 → 转字符串（含 0 不是 fallback）
 *  - 用例：metrics 缺失、JSON 反序列化差异等导致的渲染崩溃防护
 */
export function safeNum(v: unknown, fallback: string = "--"): string {
  if (v === undefined || v === null) return fallback;
  const n = Number(v);
  return isNaN(n) ? fallback : String(n);
}

/** 安全字符串转换：unknown → string。
 *  - undefined / null / 空字符串 → fallback
 */
export function safeStr(v: unknown, fallback: string = "--"): string {
  if (v === undefined || v === null || v === "") return fallback;
  return String(v);
}

export function formatTime(iso: string): string {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

export function formatRelative(iso: string, now: Date = new Date()): string {
  if (!iso) return "--";
  const d = new Date(iso);
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function formatBoth(iso: string): string {
  return `${formatRelative(iso)} · ${formatTime(iso)}`;
}

export function truncate(s: string, n: number): string {
  if (!s) return "--";
  return s.length > n ? s.slice(0, n) + "..." : s;
}

export function formatCost(usd: number | undefined | null): string {
  if (usd === undefined || usd === null) return "--";
  if (usd === 0) return "$0.00";
  if (usd < 0.0001) return `$${(usd * 1_000_000).toFixed(2)}µ`;
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

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
  if (ms === undefined) return traces;
  return traces.filter((t) => {
    const ts = new Date(t.timestamp).getTime();
    return now - ts <= ms;
  });
}

export function severityStyle(sev: AlertItem["severity"]): { bg: string; text: string; label: string } {
  switch (sev) {
    case "critical": return { bg: "bg-red-100 text-red-700", text: "text-red-700", label: "CRITICAL" };
    case "error":    return { bg: "bg-orange-100 text-orange-700", text: "text-orange-700", label: "ERROR" };
    case "warning":  return { bg: "bg-amber-100 text-amber-700", text: "text-amber-700", label: "WARNING" };
  }
}

// ── Span 工具函数 ────────────────────────────────────

/** 扁平化 Span 树为列表（BFS） */
export function flattenSpans(spans: Span[]): Span[] {
  const result: Span[] = [];
  const queue = [...spans.filter(s => s.parent_id === null)];
  while (queue.length > 0) {
    const span = queue.shift()!;
    result.push(span);
    const children = span.children.map(cid => spans.find(s => s.id === cid)).filter(Boolean) as Span[];
    queue.push(...children);
  }
  return result;
}

/** 从 spans 数组中找某个 span */
export function findSpan(spans: Span[], id: string): Span | undefined {
  return spans.find(s => s.id === id);
}

/** 按 type 过滤 spans */
export function filterSpansByType(spans: Span[], type: string): Span[] {
  return spans.filter(s => s.type === type);
}

/** 构建 span 父子映射 */
export function buildSpanTree(spans: Span[]): Map<string, Span[]> {
  const map = new Map<string, Span[]>();
  for (const s of spans) {
    const key = s.parent_id ?? "__root__";
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(s);
  }
  return map;
}
