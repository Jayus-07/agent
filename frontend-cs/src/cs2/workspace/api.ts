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
  cs_role?: string | null
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

// ── P4：快捷回复话术（个人/团队） ───────────────────────────

export type CannedScope = 'personal' | 'team'

export interface CannedItem {
  id: number
  scope: CannedScope | string
  owner_agent_id: string | null
  title: string
  content: string
  category: string
  enabled: boolean
  updated_at: string | null
}

export interface CannedMutationResult {
  id: number
  scope: string
  title: string
  content: string
  category: string
  enabled: boolean
}

export async function listCanned(): Promise<CannedItem[]> {
  const data = await request<{ items: CannedItem[] }>('/api/cs/canned-responses')
  return data.items ?? []
}

export async function createCanned(body: {
  title: string
  content: string
  category?: string
  scope: CannedScope
}): Promise<CannedMutationResult> {
  return request('/api/cs/canned-responses', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function updateCanned(
  id: number,
  body: { title?: string; content?: string; category?: string; enabled?: boolean },
): Promise<CannedMutationResult> {
  return request(`/api/cs/canned-responses/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function deleteCanned(id: number): Promise<void> {
  await request(`/api/cs/canned-responses/${id}`, { method: 'DELETE' })
}

// ── P5：退款审核（supervisor） ─────────────────────────────

export interface RefundQueueItem {
  request_id: string
  request_no?: string | null
  status: string
  status_display?: string | null
  order_no?: string | null
  amount?: string | number | null
  reason?: string | null
  policy_version?: string | null
  user_id?: string | null
  conversation_id?: string | null
  review?: { decision: string; opinion: string; reviewer_id: string; reviewed_at?: string } | null
  created_at?: string | null
  updated_at?: string | null
}

export async function listRefundQueue(
  status?: string,
): Promise<{ items: RefundQueueItem[]; total: number; mode: string }> {
  const q = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/api/cs/refunds/review-queue${q}`)
}

export async function reviewRefund(
  requestId: string,
  decision: 'approved' | 'rejected',
  opinion: string,
): Promise<{ request_id: string; status: string; status_display?: string | null }> {
  return request(`/api/cs/refunds/${requestId}/review`, {
    method: 'POST',
    body: JSON.stringify({ decision, opinion }),
  })
}

// ── cs-workstation-v2 P6：质检中心 ─────────────────────────────

export type QaDimKey = 'service' | 'accuracy' | 'compliance' | 'timeliness'

export interface QaScores {
  service: number | null
  accuracy: number | null
  compliance: number | null
  timeliness: number | null
}

export interface QaAiPrefill {
  scores: Record<QaDimKey, number>
  total: number
  verdict: 'pass' | 'fail'
  note: string
}

export interface QaTask {
  id: number
  conversation_id: string
  agent_id: string
  batch_date: string | null
  source: 'ai' | 'human'
  status: 'pending' | 'draft' | 'scored'
  scores: QaScores
  total_score: number | null
  verdict: 'pass' | 'fail' | null
  note: string | null
  ai_prefill: QaAiPrefill | null
  scored_by: string | null
  scored_at: string | null
  appeal_status: 'none' | 'appealing' | 'upheld' | 'overturned'
  appeal_reason: string | null
  appeal_requested_by: string | null
  appeal_decision: 'upheld' | 'overturned' | null
  appeal_opinion: string | null
  final_score: number | null
  final_verdict: 'pass' | 'fail' | null
  created_at: string | null
}

export interface QaScoreInput {
  service: number
  accuracy: number
  compliance: number
  timeliness: number
  verdict: 'pass' | 'fail'
  note?: string
  draft?: boolean
}

export interface QaAppealInput {
  reason: string
}

export async function listQaTasks(params?: {
  status?: string
  appeal?: string
}): Promise<{ items: QaTask[]; total: number; scope: 'self' | 'all' }> {
  const search = new URLSearchParams()
  if (params?.status) search.set('status', params.status)
  if (params?.appeal) search.set('appeal', params.appeal)
  const qs = search.toString()
  return request(`/api/cs/qa/tasks${qs ? `?${qs}` : ''}`)
}

export async function submitQaScore(
  taskId: number,
  body: QaScoreInput,
): Promise<{ task: QaTask }> {
  return request(`/api/cs/qa/tasks/${taskId}/score`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function requestQaAiPrefill(
  taskId: number,
): Promise<{ task: QaTask }> {
  return request(`/api/cs/qa/tasks/${taskId}/ai-prefill`, { method: 'POST' })
}

export async function appealQaScore(
  taskId: number,
  reason: string,
): Promise<{ task: QaTask }> {
  return request(`/api/cs/qa/tasks/${taskId}/appeal`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export async function decideQaAppeal(
  taskId: number,
  body: {
    decision: 'upheld' | 'overturned'
    opinion: string
    final_score?: number
    final_verdict?: 'pass' | 'fail'
  },
): Promise<{ task: QaTask }> {
  return request(`/api/cs/qa/tasks/${taskId}/appeal/decision`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export interface QaReportRow {
  batch_date: string
  sampled: number
  scored: number
  pass_count: number
  pass_rate: number | null
  avg_score: number | null
  appeals: number
  top_deduction: string | null
}

export async function getQaReport(days = 7): Promise<{ items: QaReportRow[] }> {
  return request(`/api/cs/qa/report?days=${days}`)
}
