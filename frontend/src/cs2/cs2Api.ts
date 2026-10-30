// cs-workstation-v2 P1：用户端客服 API（复用 @/api/client 基建，路径经 BFF）。
import { nanoid } from 'nanoid'
import { request } from '@/api/client'

export interface MyConversationMessage {
  message_id: string
  sender_type: string
  content: string
  content_type: string
  created_at: string
}

export interface MyConversationItem {
  conversation_id: string
  summary?: string | null
  conversation_status: string
  handling_mode: string
  created_at: string
  last_activity_at?: string | null
  messages: MyConversationMessage[]
}

export interface HandoffMessagesResponse {
  conversation_id: string
  handoff_state: string
  last_id: number
  messages: Array<{
    message_id: string
    sender_type: string
    content: string
    content_type: string
    created_at: string
  }>
  agent_typing: boolean
  handoff_meta?: {
    handoff_id: string
    handoff_state: string
    total_deadline_at: string | null
    queue_position: number
  } | null
}

export interface CustomerContext {
  customer: {
    display_name: string
    gender?: string | null
    level?: string | null
    register_time?: string | null
  }
  orders: Array<{
    id: string
    order_no: string
    total_amount?: number | null
    status: string
    payment_status?: string | null
    created_at?: string | null
    product_summary: string
  }>
  demo_mode: boolean
  mock_data: boolean
  profile_unavailable: boolean
}

/** 生成新会话 ID（= conversation_id，首轮随请求提交） */
export function newSessionId(): string {
  return nanoid(21)
}

export async function fetchMyConversations(limit = 10): Promise<MyConversationItem[]> {
  const data = await request<{ items: MyConversationItem[] }>(
    `/api/cs/conversations/my?limit=${limit}`,
  )
  return data.items ?? []
}

export async function fetchMyMessages(
  conversationId: string,
  sinceId = 0,
): Promise<HandoffMessagesResponse> {
  return request(`/api/cs/conversations/my/${conversationId}/messages?since_id=${sinceId}`)
}

export async function requestHandoff(conversationId: string): Promise<{ handoff_state: string }> {
  return request(`/api/cs/conversations/${conversationId}/handoff`, {
    method: 'POST',
    headers: { 'Idempotency-Key': nanoid(21) },
  })
}

export async function submitRating(
  conversationId: string,
  rating: number,
  comment?: string,
): Promise<void> {
  await request(`/api/cs/conversations/${conversationId}/rating`, {
    method: 'POST',
    body: JSON.stringify({ rating, comment: comment || undefined }),
  })
}

export async function fetchCustomerContext(): Promise<CustomerContext> {
  return request('/api/cs/customer-context')
}

export const ORDER_STATUS_ZH: Record<string, string> = {
  pending: '待付款',
  paid: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunded: '已退款',
}
