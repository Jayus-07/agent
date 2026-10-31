// cs-workstation-v2 P1：坐席工作台 API（复用 @/api/client，路径经 BFF）。
import { request } from '@/api/client'

export interface HandoffQueueItem {
  conversation_id: string
  user_id: string
  handoff_state: string
  handoff_id: string
  trigger_type: string
  trigger_reason: string | null
  updated_at: string
  last_message_preview: string | null
}

export interface HandoffQueueResponse {
  items: HandoffQueueItem[]
  total: number
}

export interface MessageDTO {
  message_id: string
  sender_type: string
  content: string
  content_type: string
  intent_domain?: string | null
  intent_name?: string | null
  confidence?: number | null
  trace_id?: string | null
  created_at: string
}

export interface ConversationDetail {
  conversation_id: string
  user_id: string
  conversation_status: string
  handling_mode: string
  priority: string
  channel: string
  summary: string | null
  context_summary: string | null
  trace_count: number
  last_trace_id: string | null
  created_at: string
  last_activity_at: string
  messages: MessageDTO[]
}

export interface HandoffMessagesResponse {
  conversation_id: string
  handoff_state: string
  last_id: number
  messages: MessageDTO[]
  agent_typing: boolean
  handoff_meta?: {
    handoff_id: string
    handoff_state: string
    total_deadline_at: string | null
    queue_position: number
  } | null
}

export interface ConversationEvent {
  seq: number
  event_id: string
  type: string
  payload: Record<string, unknown>
  ts: string
}

export async function getHandoffQueue(states?: string): Promise<HandoffQueueResponse> {
  const q = states ? `?states=${encodeURIComponent(states)}` : ''
  return request(`/api/cs/conversations/handoff/queue${q}`)
}

export async function getConversationDetail(conversationId: string): Promise<ConversationDetail> {
  return request(`/api/cs/conversations/${conversationId}`)
}

export async function getHandoffMessages(
  conversationId: string,
  sinceId = 0,
  limit = 200,
): Promise<HandoffMessagesResponse> {
  return request(`/api/cs/conversations/${conversationId}/messages?since_id=${sinceId}&limit=${limit}`)
}

/** 断线补发：只返回 seq > after_seq 的已持久化事件（升序） */
export async function getConversationEvents(
  conversationId: string,
  afterSeq = 0,
  limit = 200,
): Promise<{ conversation_id: string; after_seq: number; events: ConversationEvent[] }> {
  return request(`/api/cs/conversations/${conversationId}/events?after_seq=${afterSeq}&limit=${limit}`)
}

export async function claimConversation(conversationId: string): Promise<{
  conversation_id: string
  handoff_state: string
  agent_id: string
  already_claimed: boolean
}> {
  return request(`/api/cs/conversations/${conversationId}/claim`, {
    method: 'POST',
    body: JSON.stringify({ agent_id: '' }),
  })
}

export async function closeConversation(conversationId: string): Promise<{
  conversation_id: string
  handoff_state: string
  closed_by: string
}> {
  return request(`/api/cs/conversations/${conversationId}/close`, {
    method: 'POST',
    body: JSON.stringify({ agent_id: '' }),
  })
}

export async function sendAgentMessage(
  conversationId: string,
  content: string,
): Promise<{ message_id: string; sender_type: string; content: string; created_at: string }> {
  return request(`/api/cs/conversations/${conversationId}/agent-messages`, {
    method: 'POST',
    body: JSON.stringify({ agent_id: '', content }),
  })
}

export async function issueWsTicket(): Promise<{ ticket: string; ws_path: string; ttl: number }> {
  return request('/api/cs/conversations/agent/ws-ticket', { method: 'POST' })
}

// ── P2：待确认提案快照 + 客户360 ────────────────────────────

export interface PendingProposal {
  proposal_id: string
  version: number
  action_type: string
  target_type: string
  target_id: string
  summary: string
  reason: string
  expires_at: string | null
  state: 'pending' | 'paused_handoff' | 'expired' | string
}

export async function getPendingProposal(
  conversationId: string,
): Promise<PendingProposal | null> {
  const data = await request<{ pending_action: PendingProposal | null }>(
    `/api/cs/conversations/${conversationId}/pending-proposal`,
  )
  return data.pending_action ?? null
}

export interface ConversationCustomerContext {
  customer: { display_name: string; level?: string | null; register_time?: string | null }
  orders: Array<{
    order_no: string
    total_amount: number | null
    status: string
    payment_status: string | null
    created_at: string | null
    product_summary: string
  }>
  balance: { balance: number; currency: string } | null
}

export async function getConversationCustomerContext(
  conversationId: string,
): Promise<ConversationCustomerContext> {
  return request(`/api/cs/conversations/${conversationId}/customer-context`)
}

// ── P3：坐席状态 / 转接 / offer ─────────────────────────────

export type AgentWorkStatus = 'online' | 'busy' | 'away' | 'offline'

export interface AgentStatusView {
  agent_id: string
  display_name?: string
  status: AgentWorkStatus | string
  accepting: boolean
  skill?: string
  presence_online: boolean
}

export async function getMyStatus(): Promise<AgentStatusView> {
  return request('/api/cs/agents/me/status')
}

export async function updateMyStatus(
  status: AgentWorkStatus,
): Promise<AgentStatusView> {
  return request('/api/cs/agents/me/status', {
    method: 'PUT',
    body: JSON.stringify({ status }),
  })
}

export interface TransferResult {
  conversation_id: string
  handoff_state: string
  from_agent_id: string
  to_agent_id: string
}

export async function transferConversation(
  conversationId: string,
  targetAgentId: string,
  reason: string,
): Promise<TransferResult> {
  return request(`/api/cs/conversations/${conversationId}/transfer`, {
    method: 'POST',
    body: JSON.stringify({ target_agent_id: targetAgentId, reason }),
  })
}

export interface OfferItem {
  handoff_id: string
  conversation_id: string
  user_id: string
  handoff_state: string
  assignment_version: number
  attempt_count: number
  priority?: number
  offered_at?: string
  offer_expires_at?: string
}

export async function listMyOffers(): Promise<OfferItem[]> {
  const data = await request<{ items: OfferItem[] }>('/api/cs/agents/me/offers')
  return data.items ?? []
}

export async function acceptOffer(
  handoffId: string,
  offerVersion?: number,
): Promise<{ conversation_id: string; handoff_state: string }> {
  return request(`/api/cs/agents/me/offers/${handoffId}/accept`, {
    method: 'POST',
    body: JSON.stringify({ offer_version: offerVersion }),
  })
}

export async function declineOffer(
  handoffId: string,
  offerVersion?: number,
): Promise<{ conversation_id: string; handoff_state: string }> {
  return request(`/api/cs/agents/me/offers/${handoffId}/decline`, {
    method: 'POST',
    body: JSON.stringify({ offer_version: offerVersion }),
  })
}
