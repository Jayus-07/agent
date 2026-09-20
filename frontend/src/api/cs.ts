import { request, fetchRaw } from "@/lib/fetcher";
import type {
  PaginatedConversations,
  ConversationDetail,
  ConversationTracesResponse,
} from "@/types/cs";

export interface MyConversationMessage {
  message_id: string;
  sender_type: string;
  content: string;
  content_type?: string;
  created_at: string;
}

export interface MyConversationItem {
  conversation_id: string;
  summary?: string | null;
  conversation_status: string;
  handling_mode: string;
  created_at: string;
  last_activity_at?: string | null;
  messages: MyConversationMessage[];
}

export interface MyConversationsResponse {
  items: MyConversationItem[];
}

export interface HandoffResponse {
  handoff_id: string;
  conversation_id: string;
  handoff_state: 'waiting_human' | 'agent_offered' | 'human_active' | 'closed';
  total_deadline_at: string | null;
  reused: boolean;
}

/**
 * 用户显式请求人工客服。
 *
 * 幂等键放在请求头而不是请求体；后端 PostgreSQL 活动工单是最终事实源，
 * 网络超时后的重试即使换了前端调用栈，也只会复用同一活动工单。
 */
export async function requestHandoff(
  conversationId: string,
  idempotencyKey: string,
): Promise<HandoffResponse> {
  return request<HandoffResponse>(
    `/api/cs/conversations/${encodeURIComponent(conversationId)}/handoff`,
    {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
    },
  );
}

/**
 * 用户侧「我的客服会话」（含消息）——客服抽屉刷新后恢复历史。
 * 后端按网关注入身份过滤，guest 返回 401（此时前端静默跳过恢复）。
 */
export async function listMyConversations(limit = 10): Promise<MyConversationItem[]> {
  try {
    const res = await fetchRaw(`/api/cs/conversations/my?limit=${limit}`);
    if (!res.ok) return [];
    const data = (await res.json()) as MyConversationsResponse;
    return data.items || [];
  } catch {
    return [];
  }
}

/**
 * 用户「输入中」上报（瞬态，服务端 TTL 5s）：坐席工作台经 WS 实时可见。
 * 静默失败——提示属体验增强，不阻塞输入主链路。
 */
export async function notifyUserTyping(conversationId: string): Promise<void> {
  try {
    await fetchRaw(
      `/api/cs/conversations/my/${encodeURIComponent(conversationId)}/typing`,
      { method: "POST" },
    );
  } catch {
    // 静默
  }
}

export async function listConversations(params: {
  limit?: number;
  cursor?: string;
  status?: string;
  handling_mode?: string;
  user_id?: string;
  q?: string;
} = {}): Promise<PaginatedConversations> {
  const sp = new URLSearchParams();
  if (params.limit) sp.set("limit", String(params.limit));
  if (params.cursor) sp.set("cursor", params.cursor);
  if (params.status) sp.set("status", params.status);
  if (params.handling_mode) sp.set("handling_mode", params.handling_mode);
  if (params.user_id) sp.set("user_id", params.user_id);
  if (params.q) sp.set("q", params.q);
  try {
    return await request<PaginatedConversations>(
      `/api/cs/conversations?${sp.toString()}`,
    );
  } catch (e) {
    throw new Error(`listConversations failed: ${(e as Error).message}`);
  }
}

export async function getConversation(
  conversationId: string,
): Promise<ConversationDetail | null> {
  try {
    return await request<ConversationDetail>(
      `/api/cs/conversations/${encodeURIComponent(conversationId)}`,
    );
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 404) return null;
    throw new Error(`getConversation failed: ${(e as Error).message}`);
  }
}

export async function getConversationTraces(
  conversationId: string,
): Promise<ConversationTracesResponse> {
  try {
    return await request<ConversationTracesResponse>(
      `/api/cs/conversations/${encodeURIComponent(conversationId)}/traces`,
    );
  } catch (e) {
    throw new Error(`getConversationTraces failed: ${(e as Error).message}`);
  }
}


// ========================================
// P3.1: 确认卡片交互（CSConfirmCard → POST /cs/confirm）
// ========================================

export interface ConfirmActionResponse {
  status: 'success' | 'failed' | 'cancelled' | 'expired' | 'duplicate'
  answer: string
  confirmation_state: string
  action_result?: Record<string, unknown> | null
}

/**
 * 确认/取消待执行操作。幂等：并发重复提交由后端原子认领闸门兜底，
 * 已处理的提交返回 409（调用方静默清卡片即可）。
 */
export async function confirmAction(
  sessionId: string,
  decision: 'confirm' | 'cancel',
): Promise<ConfirmActionResponse> {
  return request<ConfirmActionResponse>('/api/cs/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, decision }),
  })
}
