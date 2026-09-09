export const CS_NODE_LABELS: Record<string, string> = {
  cs_knowledge: '知识检索',
  cs_business_query: '业务查询',
  cs_business_action: '业务操作',
  cs_complaint: '投诉处理',
  cs_handoff: '转接人工',
  cs_handoff_intercept: '转接拦截',
  cs_pending: '待处理',
}

export const CS_NODE_ICONS: Record<string, string> = {
  cs_knowledge: '📚',
  cs_business_query: '🔍',
  cs_business_action: '⚡',
  cs_complaint: '📋',
  cs_handoff: '🤝',
  cs_handoff_intercept: '🛡️',
  cs_pending: '⏳',
}

export const CS_QUICK_PROMPTS = [
  { label: '查询订单', icon: '📦', prompt: '帮我查一下最近的订单状态' },
  { label: '物流追踪', icon: '🚚', prompt: '我的快递到哪了？' },
  { label: '申请退款', icon: '💰', prompt: '我想申请退款' },
  { label: '投诉建议', icon: '📝', prompt: '我要投诉一个问题' },
  { label: '转接人工', icon: '👤', prompt: '我想转接人工客服' },
]

export type CSConfirmationState = 'none' | 'pending' | 'confirmed' | 'cancelled'
export type CSHandoffState = 'none' | 'requested' | 'waiting' | 'active' | 'closed'
