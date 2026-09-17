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

// ── 演示模式快捷提问（SB-3）────────────────────────
// 由构建期环境变量 NEXT_PUBLIC_CS_DEMO_MODE 控制：
// demo 模式下问题携带预置演示订单号（DEMO- 前缀，与后端
// backend/sql/seeds/demo_sandbox.sql 播种数据对应），保证一键命中剧本；
// 真实模式维持泛化问法。演示方案见 docs/customer-service/演示沙盒方案-2026-09-17.md
const DEMO_MODE = process.env.NEXT_PUBLIC_CS_DEMO_MODE === 'true'

export const CS_QUICK_PROMPTS = DEMO_MODE
  ? [
      { label: '查询订单', icon: '📦', prompt: '帮我查一下订单 DEMO-1001 的状态' },
      { label: '物流追踪', icon: '🚚', prompt: '我的订单 DEMO-1003 包裹好几天没更新了，帮我看看怎么回事' },
      { label: '申请退款', icon: '💰', prompt: '订单 DEMO-1002 的商品坏了，我想退款，应该怎么操作？' },
      { label: '投诉建议', icon: '📝', prompt: '我要投诉一个问题' },
      { label: '转接人工', icon: '👤', prompt: '我想转接人工客服' },
    ]
  : [
      { label: '查询订单', icon: '📦', prompt: '帮我查一下最近的订单状态' },
      { label: '物流追踪', icon: '🚚', prompt: '我的快递到哪了？' },
      { label: '申请退款', icon: '💰', prompt: '我想申请退款' },
      { label: '投诉建议', icon: '📝', prompt: '我要投诉一个问题' },
      { label: '转接人工', icon: '👤', prompt: '我想转接人工客服' },
    ]

export type CSConfirmationState = 'none' | 'pending' | 'confirmed' | 'cancelled'
export type CSHandoffState = 'none' | 'requested' | 'waiting' | 'active' | 'closed'
