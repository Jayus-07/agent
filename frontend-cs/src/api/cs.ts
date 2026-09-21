import { request } from "@/lib/fetcher";
import type {
  PaginatedConversations,
  ConversationDetail,
  ConversationTracesResponse,
  HandoffQueueResponse,
  HandoffClaimResult,
  HandoffMessageDTO,
  HandoffMessagesResponse,
  CSStatsResponse,
} from "@/types/cs";

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

// ── 人工介入（坐席工作台，v1 轮询）────────────────────

export async function getHandoffQueue(
  states?: string,
): Promise<HandoffQueueResponse> {
  const sp = states ? `?states=` + encodeURIComponent(states) : "";
  return request<HandoffQueueResponse>(`/api/cs/conversations/handoff/queue` + sp);
}

export async function claimConversation(
  conversationId: string,
): Promise<HandoffClaimResult> {
  return request<HandoffClaimResult>(
    `/api/cs/conversations/` + encodeURIComponent(conversationId) + `/claim`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // P7：坐席身份由后端从登录身份反查，不再提交 agent_id。
      body: JSON.stringify({}),
    },
  );
}

export async function sendAgentMessage(
  conversationId: string,
  content: string,
): Promise<HandoffMessageDTO> {
  return request<HandoffMessageDTO>(
    `/api/cs/conversations/` + encodeURIComponent(conversationId) + `/agent-messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
}

export async function getHandoffMessages(
  conversationId: string,
  sinceId: number,
): Promise<HandoffMessagesResponse> {
  return request<HandoffMessagesResponse>(
    `/api/cs/conversations/` + encodeURIComponent(conversationId) + `/messages?since_id=` + sinceId,
  );
}

// ── 人工介入 v2（WS 推送 + 关闭）────────────────────

export async function issueWsTicket(): Promise<{
  ticket: string;
  ws_path: string;
  ttl: number;
}> {
  return request<{ ticket: string; ws_path: string; ttl: number }>(
    `/api/cs/conversations/agent/ws-ticket`,
    { method: "POST" },
  );
}

export async function closeConversation(
  conversationId: string,
): Promise<{ conversation_id: string; handoff_state: string; closed_by: string }> {
  return request(
    `/api/cs/conversations/` + encodeURIComponent(conversationId) + `/close`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    },
  );
}

// ── P7 坐席 offer（待接单/拒单）────────────────────────

export interface MyOfferItem {
  handoff_id: string;
  conversation_id: string;
  user_id: string;
  handoff_state: string;
  assignment_version: number;
  attempt_count: number;
  priority: number;
  offered_at: string | null;
  offer_expires_at: string | null;
}

export async function getMyOffers(): Promise<{
  items: MyOfferItem[];
  total: number;
}> {
  return request(`/api/cs/agents/me/offers`);
}

export async function acceptOffer(
  handoffId: string,
  offerVersion?: number,
): Promise<{
  handoff_id: string;
  conversation_id: string;
  handoff_state: string;
  agent_id: string | null;
  assignment_version: number;
  offer_expires_at: string | null;
}> {
  return request(
    `/api/cs/agents/me/offers/` + encodeURIComponent(handoffId) + `/accept`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        offerVersion === undefined ? {} : { offer_version: offerVersion },
      ),
    },
  );
}

export async function declineOffer(
  handoffId: string,
  offerVersion?: number,
): Promise<{
  handoff_id: string;
  conversation_id: string;
  handoff_state: string;
  agent_id: string | null;
  assignment_version: number;
  offer_expires_at: string | null;
}> {
  return request(
    `/api/cs/agents/me/offers/` + encodeURIComponent(handoffId) + `/decline`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        offerVersion === undefined ? {} : { offer_version: offerVersion },
      ),
    },
  );
}

// ── 满意度统计（014_cs_rating）────────────────────

export async function getCSStats(): Promise<CSStatsResponse> {
  try {
    return await request<CSStatsResponse>(`/api/cs/conversations/stats`);
  } catch (e) {
    throw new Error(`getCSStats failed: ${(e as Error).message}`);
  }
}

/**
 * 坐席「输入中」上报（瞬态，服务端 TTL 5s）：用户端轮询响应 agent_typing 可见。
 * 静默失败——提示属体验增强，不阻塞坐席输入主链路。
 */
export async function notifyAgentTyping(
  conversationId: string,
): Promise<void> {
  try {
    await request(
      `/api/cs/conversations/${encodeURIComponent(conversationId)}/typing`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      },
    );
  } catch {
    // 静默
  }
}
