/**
 * context-summary — 会话 Agent 工作上下文的轻量解析工具
 *
 * 两条规则：
 *  1. 后端把 context_summary 持久化为 JSON 字符串；前端用 Map 缓存 parse 结果，
 *     避免在大列表（HistorySidebar / tasks 页面）中每个 session 都执行一次 JSON.parse。
 *  2. 解析失败不能阻塞 UI —— 返回 null，让上层按"无上下文"路径渲染。
 */
export interface ContextSummary {
  sql_results?: number;
  rag_docs?: number;
  last_report?: string;
  turns?: number;
  [key: string]: unknown;
}

// 单页面 session 数量有限，Map 不会泄漏；如未来进入百万级会话需切换 LRU
const cache = new Map<string, ContextSummary | null>();

export function parseContextSummary(raw: string | null | undefined): ContextSummary | null {
  if (!raw) return null;
  if (cache.has(raw)) return cache.get(raw) ?? null;
  let parsed: ContextSummary | null = null;
  try {
    parsed = JSON.parse(raw) as ContextSummary;
  } catch {
    parsed = null;
  }
  cache.set(raw, parsed);
  return parsed;
}
