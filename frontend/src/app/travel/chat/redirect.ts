/**
 * /travel/chat 旧链接重定向目标计算（决策 5：chat 独立生成页废弃，P4b）。
 *
 * 独立模块的原因：Next.js Page 文件只允许 default/配置类导出，纯函数
 * 放 page.tsx 会导致生产构建失败；测试也从这里导入。
 *
 * 生成流唯一入口 = 工作台生成态 /travel/itineraries/new；正式行程对话 =
 * 工作台右列 TravelV2TripConversation。本模块只做 URL 映射：
 * - 带 trip_id → 正式工作台（conversation_id 一并透传；工作台只认服务端
 *   验证的会话关联，URL 候选不作为关联依据）；
 * - 其余（prompt / conversation_id / 裸访问）→ 工作台生成态，query 保留。
 */
export function travelChatRedirectHref(query: { prompt?: string; conversation_id?: string; trip_id?: string }): string {
  const tripId = query.trip_id?.trim() ?? ''
  if (tripId) {
    const conversationId = query.conversation_id?.trim() ?? ''
    return `/travel/itineraries/${encodeURIComponent(tripId)}${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''}`
  }
  const prompt = query.prompt ?? ''
  const conversationId = query.conversation_id?.trim() ?? ''
  const params = new URLSearchParams()
  if (prompt) params.set('prompt', prompt)
  if (conversationId) params.set('conversation_id', conversationId)
  const suffix = params.toString()
  return `/travel/itineraries/new${suffix ? `?${suffix}` : ''}`
}
