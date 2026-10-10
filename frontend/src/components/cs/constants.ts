/**
 * CS 流程节点标识 → 展示映射。
 *
 * 键 = **后端真实节点名**，真源两处（不得在此另写一份）：
 *   - 主图注册名 `cs_graph_node`（backend/customer_service/register.py）——SSE
 *     `status.node` 的当前实际值；
 *   - CS 子图内部节点名（backend/customer_service/graph_state.py 的 `CS_*` 常量）。
 *
 * ⚠️ 当前后端只把**主图节点名**下发到 SSE（主图 stream 走到域图节点只有一步），
 * 子图内部那 9 个节点名暂时收不到——先按真源登记，待后端转发子图事件后自动生效。
 *
 * 历史坑（2026-09-30 修复）：这套键原为旧架构名
 * `cs_knowledge` / `cs_business_query` / …，与后端**任何**真实节点都对不上，
 * 导致状态栏显示「正在 cs_graph_node...」、时间轴图标恒为 `•`、
 * 意图标签永不显示。
 *
 * 守护：backend/tests/test_frontend_node_ids_consistency.py 断言本文件每个键
 * 都存在于后端真实节点集——写错名会被测试咬住。
 */
export const CS_NODE_LABELS: Record<string, string> = {
  cs_graph_node: '客服图执行',
  cs_state_loader: '状态加载',
  cs_supervisor: '客服调度',
  cs_knowledge_expert: '知识检索',
  cs_query_expert: '业务查询',
  cs_action_expert: '业务操作',
  cs_complaint_expert: '投诉处理',
  cs_handoff_expert: '转接人工',
  cs_pending_handler: '待处理',
  cs_reporter: '客服汇总',
}

export const CS_NODE_ICONS: Record<string, string> = {
  cs_graph_node: '💬',
  cs_state_loader: '📥',
  cs_supervisor: '🧭',
  cs_knowledge_expert: '📚',
  cs_query_expert: '🔍',
  cs_action_expert: '⚡',
  cs_complaint_expert: '📋',
  cs_handoff_expert: '🤝',
  cs_pending_handler: '⏳',
  cs_reporter: '📤',
}

// ── 演示模式快捷提问（SB-3）────────────────────────
// 由构建期环境变量 NEXT_PUBLIC_CS_DEMO_MODE 控制：
// demo 模式下问题携带预置演示订单号（DEMO- 前缀，与后端
// backend/sql/seeds/demo_sandbox.sql 播种数据对应），保证一键命中剧本；
// 真实模式维持泛化问法。演示方案见 docs/customer-service/demo-sandbox.md
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
