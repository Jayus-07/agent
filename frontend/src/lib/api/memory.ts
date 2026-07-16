/**
 * 记忆 / 会话业务 API
 */
import { request } from "../fetcher";

export interface SessionMeta {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
}

/** GET /memory/sessions — 列出所有会话 */
export async function listSessions(): Promise<SessionMeta[]> {
  try {
    const data = await request<{ sessions: SessionMeta[] }>("/memory/sessions");
    return data.sessions || [];
  } catch {
    return [];
  }
}

/** GET /memory/sessions/{id} — 获取会话消息 */
export async function getSessionMessages(sessionId: string): Promise<{ role: string; content: string }[]> {
  try {
    const data = await request<{ messages: { role: string; content: string }[] }>(
      `/memory/sessions/${encodeURIComponent(sessionId)}`,
    );
    return data.messages || [];
  } catch {
    return [];
  }
}

/** DELETE /memory/sessions/{id} — 删除会话 */
export async function deleteMemorySession(sessionId: string): Promise<boolean> {
  try {
    const data = await request<{ ok: boolean }>(`/memory/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    return data.ok === true;
  } catch {
    return false;
  }
}

/** PATCH /memory/sessions/{id} — 重命名会话 */
export async function renameMemorySession(sessionId: string, title: string): Promise<boolean> {
  try {
    const data = await request<{ ok: boolean }>(`/memory/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    return data.ok === true;
  } catch {
    return false;
  }
}