import { redirect } from 'next/navigation'
import { travelChatRedirectHref } from './redirect'

/**
 * /travel/chat 旧链接兼容重定向路由（决策 5：chat 独立生成页废弃，P4b）。
 * 目标映射规则见同目录 redirect.ts。
 */
export default async function TravelChatRedirectRoute({ searchParams }: { searchParams: Promise<{ prompt?: string; conversation_id?: string; trip_id?: string }> }) {
  const query = await searchParams
  redirect(travelChatRedirectHref(query))
}
