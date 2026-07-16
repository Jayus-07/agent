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

// ── Traces ────────────────────────────────────────────

/** GET /observability/traces?limit=N — 最近 N 条 trace */
export async function listTraces(limit = 50): Promise<TraceRecord[]> {
  try {
    const data = await request<{ traces: TraceRecord[] }>(`/observability/traces?limit=${limit}`);
    return data.traces || [];
  } catch (e) {
    throw new Error(`listTraces failed: ${(e as Error).message}`);
  }
}

/** GET /observability/traces/active — 当前活跃 trace（answer_preview 为空 = 未完成） */
export async function listActiveTraces(): Promise<TraceRecord[]> {
  try {
    const data = await request<{ traces: TraceRecord[] }>("/observability/traces/active");
    return data.traces || [];
  } catch (e) {
    throw new Error(`listActiveTraces failed: ${(e as Error).message}`);
  }
}

/** GET /observability/traces/{id} — 单条 trace 完整详情（包含 spans 树） */
export async function getTraceDetail(id: string): Promise<TraceRecord | null> {
  try {
    return await request<TraceRecord>(`/observability/traces/${encodeURIComponent(id)}`);
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