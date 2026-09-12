/**
 * 可观测性业务 API：traces / metrics / resources / alerts / graph
 *
 * 数据源：FastAPI `/api/observability/*`（见 backend/app/api/routes/observability.py）
 * DTO 映射：后端 `_to_trace_dto` / `_to_span_dto` 已经做了字段名映射
 *   (span_id → id, model str → {name, provider}, 派生 duration_ratio/children/llm_call)，
 *   前端直接使用 TraceRecord/Span 类型即可。
 */
import { request } from "../fetcher";
import type { TraceRecord, AlertItem } from "@/types/trace";

// ── CS 灰度质量报告 ───────────────────────────────────

export interface CSVariantSummary {
  total: number;
  route_consistency: number | null;
  route_n?: number;
  fallback_rate: number | null;
  handoff_rate: number | null;
  handoff_by_reason: Record<string, number>;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_ms: number | null;
}

export interface CSQualityReport {
  window_hours: number;
  generated_at: string;
  total_cs_traces: number;
  by_variant: Record<string, CSVariantSummary>;
  alerts: { severity: "warning" | "error"; type: string; message: string }[];
}

/** GET /observability/cs-quality?hours=N — CS 灰度质量聚合报告 */
export async function getCsQualityReport(hours = 24): Promise<CSQualityReport | null> {
  try {
    return await request<CSQualityReport>(`/api/observability/cs-quality?hours=${hours}`);
  } catch {
    return null; // 端点不可用/未升级时静默降级，卡片显示占位
  }
}

// ── Traces ────────────────────────────────────────────

/** GET /observability/traces?limit=N&workflow_name=X — 最近 N 条 trace（服务端过滤） */
export async function listTraces(limit = 50, workflowName?: string): Promise<TraceRecord[]> {
  try {
    const wf = workflowName ? `&workflow_name=${encodeURIComponent(workflowName)}` : "";
    const data = await request<{ traces: TraceRecord[] }>(`/api/observability/traces?limit=${limit}${wf}`);
    return data.traces || [];
  } catch (e) {
    throw new Error(`listTraces failed: ${(e as Error).message}`);
  }
}

/** GET /observability/traces/stats — 时间窗聚合统计（StatsBar 下沉后端） */
export async function getTraceStats(
  hours = 24,
  workflowName?: string,
): Promise<{
  total_24h: number;
  success_rate: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
  error_count: number;
  total_cost_usd: number;
}> {
  const wf = workflowName ? `&workflow_name=${encodeURIComponent(workflowName)}` : "";
  return await request(`/api/observability/traces/stats?hours=${hours}${wf}`);
}

/** GET /observability/traces/active — 当前活跃 trace（answer_preview 为空 = 未完成） */
export async function listActiveTraces(): Promise<TraceRecord[]> {
  try {
    const data = await request<{ traces: TraceRecord[] }>("/api/observability/traces/active");
    return data.traces || [];
  } catch (e) {
    throw new Error(`listActiveTraces failed: ${(e as Error).message}`);
  }
}

/** GET /observability/traces/{id} — 单条 trace 完整详情（包含 spans 树） */
export async function getTraceDetail(id: string): Promise<TraceRecord | null> {
  try {
    return await request<TraceRecord>(`/api/observability/traces/${encodeURIComponent(id)}`);
  } catch (e) {
    // 404 → null（让页面走"不存在"分支）；其它错误抛出
    const status = (e as { status?: number }).status;
    if (status === 404) return null;
    throw new Error(`getTraceDetail failed: ${(e as Error).message}`);
  }
}

// ── Alerts（注意：后端 /alerts 字段不匹配前端 AlertItem，暂不直连） ──
// 见 lib/observability/source.ts：alerts 由 client 端 buildAlerts() 聚合 traces 而来。
// 此处保留接口签名以便将来切换到后端 AlertItem 序列化器。
export interface AlertsResponse {
  alerts: AlertItem[];
  total: number;
}

// ── Token 用量看板 ──────────────────────────────────────

export interface TokenUsageTotals {
  requests: number
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  cost_usd: number
}

export interface TokenUsageDaily {
  day: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost_usd: number
}

export interface TokenUsageByModel {
  provider: string
  model: string
  calls: number
  requests: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  cost_usd: number
}

export interface TokensSummary {
  days: number
  totals: TokenUsageTotals
  daily: TokenUsageDaily[]
  models: TokenUsageByModel[]
}

/** GET /observability/tokens/summary?days=N&component=X — Token 用量看板聚合（近 N 天） */
export async function getTokensSummary(days = 7, component?: string): Promise<TokensSummary> {
  const p = new URLSearchParams({ days: String(days) });
  if (component && component !== "all") p.set("component", component);
  return await request<TokensSummary>(`/api/observability/tokens/summary?${p.toString()}`);
}

export interface TokenCallRow {
  ts: string
  trace_id: string
  session_id: string
  component: string  // llm | embedding | rerank
  model: string
  provider: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  cost_usd: number
  duration_ms: number
  finish_reason: string
}

/** GET /observability/tokens/calls — 调用明细（分页，最新在前） */
export async function getTokensCalls(
  days = 7,
  opts: { model?: string; component?: string; limit?: number; offset?: number } = {},
): Promise<{ calls: TokenCallRow[]; total: number }> {
  const p = new URLSearchParams({ days: String(days) });
  if (opts.model) p.set("model", opts.model);
  if (opts.component && opts.component !== "all") p.set("component", opts.component);
  p.set("limit", String(opts.limit ?? 20));
  p.set("offset", String(opts.offset ?? 0));
  return await request(`/api/observability/tokens/calls?${p.toString()}`);
}
