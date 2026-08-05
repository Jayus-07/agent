/**
 * 记忆 / 会话业务 API
 *
 * 这一层不吞错：失败时抛 ApiError（由 lib/fetcher.ts 抛出），由调用方决定
 * 是展示错误态还是降级。历史教训：这里曾用 catch { return [] } 把 PostgreSQL
 * 认证失败降级成空数组，UI 显示"暂无记录"，真因只能去翻 PG 服务端日志。
 */
import { request } from "../fetcher";

export interface SessionMeta {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
  /** Agent 工作上下文摘要，JSON 字符串（无上下文时为 null） */
  context_summary?: string | null;
}

/** GET /memory/sessions — 列出所有会话 */
export async function listSessions(): Promise<SessionMeta[]> {
  const data = await request<{ sessions: SessionMeta[] }>("/memory/sessions");
  return data.sessions || [];
}

/** GET /memory/sessions/{id} — 获取会话消息 */
export async function getSessionMessages(sessionId: string): Promise<{ role: string; content: string }[]> {
  const data = await request<{ messages: { role: string; content: string }[] }>(
    `/memory/sessions/${encodeURIComponent(sessionId)}`,
  );
  return data.messages || [];
}

/** DELETE /memory/sessions/{id} — 删除会话；会话不存在时后端返回 404（抛 ApiError） */
export async function deleteMemorySession(sessionId: string): Promise<boolean> {
  const data = await request<{ ok: boolean }>(`/memory/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  return data.ok === true;
}

/** PATCH /memory/sessions/{id} — 重命名会话；会话不存在时后端返回 404（抛 ApiError） */
export async function renameMemorySession(sessionId: string, title: string): Promise<boolean> {
  const data = await request<{ ok: boolean }>(`/memory/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  return data.ok === true;
}
