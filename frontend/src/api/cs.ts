import { request } from "@/lib/fetcher";
import type {
  PaginatedConversations,
  ConversationDetail,
  ConversationTracesResponse,
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
