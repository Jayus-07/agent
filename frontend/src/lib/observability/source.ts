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

/** 列出 Agent 问答链路追踪（workflow_name === "agent"）
 *  供 /observability/traces 页面使用，只展示 /agent 智能问答产生的 trace，
 *  排除文档上传/重索引等操作日志 trace。
 *  服务端过滤（workflow_name 参数），不再拉 200 条客户端 filter。 */
export async function listAgentTraces(): Promise<TraceRecord[]> {
  if (!isClient()) return [];
  try {
    return await realApi.listTraces(200, "agent");
  } catch (e) {
    console.warn("[observability] listAgentTraces failed:", (e as Error).message);
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
