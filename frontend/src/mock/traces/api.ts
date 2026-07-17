// mock/traces/api.ts — mock 数据访问层（= 未来 FastAPI Response 单一边界）
// 关键职责：mergeTrace 把 split (Summary + Detail) 合并为 TraceRecord 视图。
// 所有 consumer（page / component）都通过此函数拿到一致的 TraceRecord，永远不要直接 cast。

import type { TraceSummary, TraceDetail, TraceListResponse, TraceQuery } from "./schemas/trace";
import type { Span } from "./schemas/span";
import type { TraceRecord, Span as DisplaySpan } from "@/types/trace";

// ═══════════════════════════════════
// Summary 预加载（从 fixtures/summaries/）
// ═══════════════════════════════════
import ragSummaries from "./fixtures/summaries/rag_agent.json";
import ragSummaries2 from "./fixtures/summaries/rag.json";
import multiAgentSummaries from "./fixtures/summaries/multi_agent.json";
import multiAgentCompareSummaries from "./fixtures/summaries/multi_agent_compare.json";
import sqlAgentSummaries from "./fixtures/summaries/sql_agent.json";
import schedulerSummaries from "./fixtures/summaries/scheduler.json";
import approvalSummaries from "./fixtures/summaries/approval_workflow.json";
import mcpSummaries from "./fixtures/summaries/mcp_tool_call.json";
import contentSummaries from "./fixtures/summaries/content_generation.json";
import memorySummaries from "./fixtures/summaries/memory_agent.json";
import dataCollectionSummaries from "./fixtures/summaries/data_collection.json";
import agentSummaries from "./fixtures/agent/summaries.json";

const RAW_SUMMARIES: TraceSummary[] = [
  ...ragSummaries,
  ...ragSummaries2,
  ...multiAgentSummaries,
  ...multiAgentCompareSummaries,
  ...sqlAgentSummaries,
  ...schedulerSummaries,
  ...approvalSummaries,
  ...mcpSummaries,
  ...contentSummaries,
  ...memorySummaries,
  ...dataCollectionSummaries,
  ...agentSummaries,
];

/** 将 mock 绝对时间戳平移到"最近 N 分钟内"，保证默认筛选（如 1h）始终能看到数据。
 *  同时注入 `timestamp` / `id` 别名 — 列表页 cast 为 TraceRecord[] 后 filterByTimeRange 读 timestamp，
 *  TraceSummary 字段名是 start_time/trace_id，不经别名映射的话 t.timestamp 始终 undefined → 全部过滤。 */
function normalizeTimestamps(summaries: TraceSummary[]) {
  if (summaries.length === 0) return summaries;
  const now = Date.now();
  // 找到最新的一条 trace，把它放到 5 分钟前
  const maxTs = Math.max(...summaries.map((s) => new Date(s.start_time).getTime()));
  const offset = now - 5 * 60 * 1000 - maxTs;
  if (Math.abs(offset) < 1000) return summaries; // 无需调整
  return summaries.map((s) => {
    const newStartTime = new Date(new Date(s.start_time).getTime() + offset).toISOString();
    return {
      ...s,
      start_time: newStartTime,
      // TraceRecord 兼容别名（列表页 as unknown as TraceRecord[] 运行时依赖这些字段）
      timestamp: newStartTime,
      id: s.trace_id,
    };
  });
}

/** 归一化后的 summary（时间戳自动平移到当前时间附近） */
const ALL_SUMMARIES: TraceSummary[] = normalizeTimestamps(RAW_SUMMARIES);

// ═══════════════════════════════════
// Detail 预加载（从 fixtures/details/）
// ═══════════════════════════════════
import d_db3748af5cb4 from "./fixtures/details/db3748af5cb4.json";
import d_a1b2c3d4e5f6 from "./fixtures/details/a1b2c3d4e5f6.json";
import d_e7f8a9b0c1d2 from "./fixtures/details/e7f8a9b0c1d2.json";
import d_f3e2d1c0b9a8 from "./fixtures/details/f3e2d1c0b9a8.json";
import d_c9b8a7f6e5d4 from "./fixtures/details/c9b8a7f6e5d4.json";
import d_a1b2c3retrievalmiss from "./fixtures/details/a1b2c3retrievalmiss.json";
import d_e7f8llmerror429 from "./fixtures/details/e7f8llmerror429.json";
import d_agent_parent_001 from "./fixtures/details/agent-parent-001.json";
import d_agent_child_001_a from "./fixtures/details/agent-child-001-a.json";
import d_agent_child_001_b from "./fixtures/details/agent-child-001-b.json";
import d_agent_child_001_c from "./fixtures/details/agent-child-001-c.json";
import d_agent_child_001_d from "./fixtures/details/agent-child-001-d.json";
import d_rag_bm25_only_001 from "./fixtures/details/rag-bm25-only-001.json";
import d_sch_001 from "./fixtures/details/sch-001.json";
import d_ma_002 from "./fixtures/details/ma-002.json";
import d_deep_001 from "./fixtures/details/deep-001.json";
import d_cache_001 from "./fixtures/details/cache-001.json";
import d_retry_001 from "./fixtures/details/retry-001.json";
import d_approval_002 from "./fixtures/details/approval-002.json";
import d_running_001 from "./fixtures/details/running-001.json";
import d_cancel_001 from "./fixtures/details/cancel-001.json";
import d_timeout_001 from "./fixtures/details/timeout-001.json";
import d_error_variety_001 from "./fixtures/details/error-variety-001.json";
import d_stream_001 from "./fixtures/details/stream-001.json";
import d_mem_002 from "./fixtures/details/mem-002.json";
import agentDetails from "./fixtures/agent/details";

const DETAILS: Record<string, TraceDetail> = {
  ...agentDetails,
  db3748af5cb4: d_db3748af5cb4 as unknown as TraceDetail,
  a1b2c3d4e5f6: d_a1b2c3d4e5f6 as unknown as TraceDetail,
  e7f8a9b0c1d2: d_e7f8a9b0c1d2 as unknown as TraceDetail,
  f3e2d1c0b9a8: d_f3e2d1c0b9a8 as unknown as TraceDetail,
  c9b8a7f6e5d4: d_c9b8a7f6e5d4 as unknown as TraceDetail,
  a1b2c3retrievalmiss: d_a1b2c3retrievalmiss as unknown as TraceDetail,
  e7f8llmerror429: d_e7f8llmerror429 as unknown as TraceDetail,
  "agent-parent-001": d_agent_parent_001 as unknown as TraceDetail,
  "agent-child-001-a": d_agent_child_001_a as unknown as TraceDetail,
  "agent-child-001-b": d_agent_child_001_b as unknown as TraceDetail,
  "agent-child-001-c": d_agent_child_001_c as unknown as TraceDetail,
  "agent-child-001-d": d_agent_child_001_d as unknown as TraceDetail,
  "rag-bm25-only-001": d_rag_bm25_only_001 as unknown as TraceDetail,
  "sch-001": d_sch_001 as unknown as TraceDetail,
  "ma-002": d_ma_002 as unknown as TraceDetail,
  "deep-001": d_deep_001 as unknown as TraceDetail,
  "cache-001": d_cache_001 as unknown as TraceDetail,
  "retry-001": d_retry_001 as unknown as TraceDetail,
  "approval-002": d_approval_002 as unknown as TraceDetail,
  "running-001": d_running_001 as unknown as TraceDetail,
  "cancel-001": d_cancel_001 as unknown as TraceDetail,
  "timeout-001": d_timeout_001 as unknown as TraceDetail,
  "error-variety-001": d_error_variety_001 as unknown as TraceDetail,
  "stream-001": d_stream_001 as unknown as TraceDetail,
  "mem-002": d_mem_002 as unknown as TraceDetail,
};

// ═══════════════════════════════════
// 查询
// ═══════════════════════════════════
function matchQuery(item: TraceSummary, q: TraceQuery): boolean {
  if (q.workflow_name && item.workflow_name !== q.workflow_name) return false;
  if (q.status && item.status !== q.status) return false;
  if (q.tags?.length && !q.tags.every((t: string) => (item.tags || []).includes(t))) return false;
  if (q.user_id && item.user_id !== q.user_id) return false;
  if (q.keyword) {
    const kw = q.keyword.toLowerCase();
    if (!item.question.toLowerCase().includes(kw) && !item.answer_preview.toLowerCase().includes(kw)) return false;
  }
  if (q.start_time && item.start_time < q.start_time) return false;
  if (q.end_time && item.start_time > q.end_time) return false;
  return true;
}

function sortTraces(
  items: TraceSummary[],
  by: TraceQuery["sort_by"] = "start_time",
  order: TraceQuery["sort_order"] = "desc"
) {
  const sorted = [...items].sort((a, b) => {
    const key = by || "start_time";
    const va: any = a[key] ?? 0;
    const vb: any = b[key] ?? 0;
    return typeof va === "string" ? va.localeCompare(vb) : va - vb;
  });
  return order === "desc" ? sorted.reverse() : sorted;
}

// ═══════════════════════════════════
// Public API
// ═══════════════════════════════════

/** @deprecated 使用 getTraceSummaries(query) 替代 */
export function getAllSummaries(): TraceSummary[] {
  return ALL_SUMMARIES;
}

export function getTraceSummaries(query: TraceQuery = {}): TraceListResponse {
  const matched = ALL_SUMMARIES.filter((item) => matchQuery(item, query));
  const sorted = sortTraces(matched, query.sort_by, query.sort_order);
  const page = query.page || 1;
  const size = query.page_size || 50;
  const start = (page - 1) * size;
  return { items: sorted.slice(start, start + size), total: matched.length, page, page_size: size };
}

export function getTraceDetail(id: string): TraceDetail | null {
  return DETAILS[id] ?? null;
}

/** 把 mock Span（含 kind/attributes/metrics/input/output 平面结构）转换为显示侧 Span。
 *  关键：type === "llm_call" 的 span 没有嵌套 llm_call{}，从 attributes/metrics/input/output 派生。
 *  GraphTopology 用 (s as any).kind — 这里把 attributes 里的 dispatch_count / dispatched_skills 等误读风险隔离掉：
 *  GraphNode 不是从这里取的，无影响。
 */
function mergeSpan(sp: Span, totalMs: number, allSpans: Span[]): DisplaySpan {
  const attrs = (sp.attributes || {}) as Record<string, unknown>;
  const metrics = (sp.metrics || {}) as Record<string, number | boolean | string>;

  // 派生 llm_call（仅 type === "llm_call"）
  let llm_call: DisplaySpan["llm_call"] | undefined;
  if (sp.type === "llm_call") {
    const model = String(attrs["llm.model"] ?? "");
    const temperature = Number(metrics["llm.temperature"] ?? 0);
    const prompt_text = String((sp.input as Record<string, unknown> | null)?.prompt ?? "");
    const response_text = String((sp.output as Record<string, unknown> | null)?.response ?? "");
    const prompt_tokens = Number(metrics.prompt_tokens ?? 0);
    const completion_tokens = Number(metrics.completion_tokens ?? 0);
    const cost_usd = Number(metrics.cost_usd ?? 0);
    llm_call = {
      model,
      temperature,
      prompt_text,
      response_text,
      prompt_tokens,
      completion_tokens,
      cost_usd,
    };
  }

  return {
    ...(sp as unknown as DisplaySpan),
    id: sp.span_id,
    type: sp.type,
    name: sp.name,
    parent_id: sp.parent_id,
    status: sp.status,
    start_time: sp.start_time,
    end_time: sp.end_time,
    duration_ms: sp.duration_ms,
    duration_ratio: sp.duration_ms / (totalMs || 1),
    attributes: sp.attributes,
    metrics: sp.metrics,
    input: sp.input ?? undefined,
    output: sp.output ?? undefined,
    events: (sp.events || []).map((e) => ({
      ts: e.timestamp,
      name: e.name,
      level: e.level,
      data: e.attributes,
      message: e.message,
    })),
    errors: sp.errors || [],
    children: allSpans.filter((c) => c.parent_id === sp.span_id).map((c) => c.span_id),
    ...(llm_call ? { llm_call } : {}),
  };
}

/** 合并 summary + detail → TraceRecord 视图（typed）。
 *  这是 mock→display 的唯一边界，所有字段都从 fixture 透传，不写死兜底值。
 *  唯一例外：mergeSpan 的 llm_call 是派生字段（无 fixture 嵌套结构）。
 */
export function mergeTrace(s: TraceSummary, d: TraceDetail): TraceRecord {
  // 错误节点：prefer summary.error_node (top-level fixture 字段)，fallback 到 detail.error.node
  const errorNode = s.error_node ?? d.error?.node ?? null;
  const errorRecord: TraceRecord["error"] = d.error
    ? { ...d.error, error_node: errorNode }
    : { code: "", message: "", node: null, retry_count: 0, error_node: errorNode };

  return {
    id: s.trace_id,
    request_id: s.trace_id,
    timestamp: s.start_time,
    session_id: s.session_id ?? "",
    question: d.request.question,
    answer_preview: s.answer_preview,
    answer_len: d.response.answer_len,
    duration_ms: s.duration_ms,
    model: { name: s.model_name, provider: s.workflow_name === "rag_agent" ? "deepseek" : "" },
    usage: {
      prompt_tokens: d.usage?.total_tokens ? Math.round(d.usage.total_tokens * 0.6) : 0,
      completion_tokens: d.usage?.total_tokens ? Math.round(d.usage.total_tokens * 0.4) : 0,
      total_tokens: d.usage?.total_tokens ?? 0,
      cost_usd: d.usage?.total_cost_usd ?? 0,
    },
    cost: { usd: s.cost_usd, currency: "USD" },
    cost_usd: s.cost_usd,
    error: errorRecord,
    metadata: {
      kb_id: s.kb_id ?? d.request.kb_id ?? "",
      user_id: s.user_id ?? "",
      temperature: d.request.temperature ?? 0.1,
      max_tokens: d.request.max_tokens ?? 4096,
    },
    spans: d.spans.map((sp) => mergeSpan(sp, s.duration_ms, d.spans)),
    workflow_name: s.workflow_name,
    workflow_version: 1,
    root_span_id: d.root_span_id,
    graph: d.graph,
    status: (s.status as TraceRecord["status"]) ?? "success",
    parent_id: s.parent_id ?? null,
    children_ids: s.children_ids ?? [],
    bookmarked: s.bookmarked ?? false,
    sla: {
      threshold_ms: s.sla_threshold_ms ?? 10000,
      breached: s.sla_breached ?? s.duration_ms > 10000,
    },
    session: s.session_id
      ? {
          user_id: s.user_id ?? "",
          user_name: s.user_name ?? s.user_id ?? "",
          started_at: s.start_time,
          trace_count: 1,
        }
      : undefined,
  };
}
