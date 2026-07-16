/**
 * 可观测性数据源切换层：Mock vs Real API
 *
 * 设计目标：页面只需改一行 import，数据源切换在底层完成。
 *
 * 切换方式：
 *   - 默认 Mock（保持现有开发体验）
 *   - 设置 `NEXT_PUBLIC_USE_MOCK=false` 切到真实 API
 *   - 真实 API 失败时静默回退 Mock（不阻断页面渲染）
 *
 * 注意：所有异步函数在 SSR 阶段返回空数组/[]，避免 Next.js 同步 IO 问题。
 */
import * as mockApi from "@/mock/traces/api";
import * as realApi from "@/lib/api/observability";
import type { TraceRecord } from "@/types/trace";

/**
 * 全局开关：NEXT_PUBLIC_USE_MOCK=false 切真实 API
 * 默认 true（开发期间避免后端停服时白屏）
 */
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== "false";

/** 服务端 / 客户端安全的数据获取：浏览器环境外走 mock 同步路径，避免 SSR 阶段 fetch */
function isClient(): boolean {
  return typeof window !== "undefined";
}

/** mock 数据（同步，从内存 JSON 读） */
function mockListAll(): TraceRecord[] {
  return mockApi.getAllSummaries() as unknown as TraceRecord[];
}

function mockGetById(id: string): TraceRecord | null {
  const s = mockApi.getAllSummaries().find((x) => x.trace_id === id);
  const d = s ? mockApi.getTraceDetail(id) : null;
  return s && d ? mockApi.mergeTrace(s, d) : null;
}

// ═══════════════════════════════════════════════════
// Public API
// ═══════════════════════════════════════════════════

/** 列出所有 trace（列表页/会话页/告警页用） */
export async function listAllTraces(): Promise<TraceRecord[]> {
  if (USE_MOCK || !isClient()) return mockListAll();
  try {
    return await realApi.listTraces(200);
  } catch (e) {
    // 降级：API 失败时静默回退 mock + 控制台 warn（不阻断渲染）
    console.warn("[observability] listAllTraces API failed, fallback to mock:", (e as Error).message);
    return mockListAll();
  }
}

/** 当前活跃 trace（实时监控面板用 — 暂未接入） */
export async function listActiveTraces(): Promise<TraceRecord[]> {
  if (USE_MOCK || !isClient()) return [];
  try {
    return await realApi.listActiveTraces();
  } catch (e) {
    console.warn("[observability] listActiveTraces failed:", (e as Error).message);
    return [];
  }
}

/** 获取单条 trace 详情（详情页/对比页/父子链用） */
export async function getTraceById(id: string): Promise<TraceRecord | null> {
  if (USE_MOCK || !isClient()) return mockGetById(id);
  try {
    return await realApi.getTraceDetail(id);
  } catch (e) {
    // 404 已由 realApi 处理为 null；这里是网络错误等
    console.warn(`[observability] getTraceById(${id}) failed, fallback to mock:`, (e as Error).message);
    return mockGetById(id);
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

/** 当前开关状态（调试用） */
export function isMockMode(): boolean {
  return USE_MOCK;
}