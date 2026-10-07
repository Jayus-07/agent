/**
 * 可观测性数据源 — 真实 API（生产环境）。
 *
 * SSR 阶段返回空数组以避免 Next.js 同步 IO 问题。
 */
import * as realApi from "@/api/observability";
import type { TraceRecord } from "@/types/trace";

/** 服务端 / 客户端安全的数据获取：浏览器环境外跳过 fetch，避免 SSR 阶段同步 IO */
function isClient(): boolean {
  return typeof window !== "undefined";
}

// ═══════════════════════════════════════════════════
// Public API
// ═══════════════════════════════════════════════════

/** 列出所有 trace（列表页/会话页/告警页用） */
export async function listAllTraces(): Promise<TraceRecord[]> {
  if (!isClient()) return [];
  try {
    return await realApi.listTraces(200);
  } catch (e) {
    console.warn("[observability] listAllTraces failed:", (e as Error).message);
    return [];
  }
}

/** 列出问答链路追踪；普通列表限定 agent，Tool 下钻跨所有入口。
 *  供 /observability/traces 页面使用：普通列表排除文档上传/重索引等操作日志；
 *  传入 hasTool 时改为全工作流检索，覆盖 travel/workflow 等直调链路。 */
export async function listAgentTraces(hasTool?: string): Promise<TraceRecord[]> {
  if (!isClient()) return [];
  try {
    // 普通 Trace 页仍只展示问答链路；Tool 下钻必须跨 agent/travel/workflow
    // 等所有入口，否则管理端会出现“统计有失败、Trace 下钻为空”。
    return await realApi.listTraces(200, hasTool ? undefined : "agent", undefined, hasTool);
  } catch (e) {
    console.warn("[observability] listAgentTraces failed:", (e as Error).message);
    return [];
  }
}

/** 列出选品漏斗运行历史（tags 含 funnel_run_id 的 trace）。
 *  漏斗域图跑在主图内，workflow_name 仍是主图，口径只能用标签：
 *  服务端 has_tag=funnel_run_id 过滤（适配器正常/异常路径都会打该标签）。 */
export async function listFunnelTraces(): Promise<TraceRecord[]> {
  if (!isClient()) return [];
  try {
    return await realApi.listTraces(100, undefined, "funnel_run_id");
  } catch (e) {
    console.warn("[observability] listFunnelTraces failed:", (e as Error).message);
    return [];
  }
}

/** Agent 链路时间窗聚合统计（StatsBar 后端下沉；失败返回 null 由页面降级自算） */
export interface AgentTraceStats {
  total_24h: number;
  success_rate: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
  error_count: number;
  total_cost_usd: number;
  /** 来源三分类计数（2026-10-08 #12，后端 trace_source 唯一分类出口） */
  sources?: { travel: number; cs: number; ai_assistant: number };
}

export async function getAgentTraceStats(hours: number): Promise<AgentTraceStats | null> {
  if (!isClient()) return null;
  try {
    return await realApi.getTraceStats(hours, "agent");
  } catch (e) {
    console.warn("[observability] getAgentTraceStats failed:", (e as Error).message);
    return null;
  }
}

/** 当前活跃 trace（实时监控面板用 — 暂未接入） */
export async function listActiveTraces(): Promise<TraceRecord[]> {
  if (!isClient()) return [];
  try {
    return await realApi.listActiveTraces();
  } catch (e) {
    console.warn("[observability] listActiveTraces failed:", (e as Error).message);
    return [];
  }
}

/** 获取单条 trace 详情（详情页/对比页/父子链用） */
export async function getTraceById(id: string): Promise<TraceRecord | null> {
  if (!isClient()) return null;
  try {
    return await realApi.getTraceDetail(id);
  } catch (e) {
    // 404 已由 realApi 处理为 null；这里是网络错误等
    console.warn(`[observability] getTraceById(${id}) failed:`, (e as Error).message);
    return null;
  }
}

/** 批量按 ID 拉详情（对比页用 — 返回 Map 便于前端查表） */
export async function getTracesByIds(ids: string[]): Promise<Map<string, TraceRecord>> {
  const map = new Map<string, TraceRecord>();
  await Promise.all(
    ids.map(async (id) => {
      const t = await getTraceById(id);
      if (t) map.set(id, t);
    }),
  );
  return map;
}
