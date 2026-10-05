'use client'

/**
 * ReplyBadge — 回复归因徽章（2026-10-05 回复呈现规范一期）
 *
 * done.reply_source 稳定码 → 中文语义徽章。只标「答案怎么来的」，
 * 架构分层（agent/skill/tool）归 trace/管理面，不上用户面。
 * 域图（客服/旅游）与闲聊后端不下发此字段，天然不渲染。
 * 灰字小徽章，不与正文抢视觉。
 */
import { BookOpen, Database, Radio, Info } from 'lucide-react'

export interface ReplySourceMeta {
  label: string
  Icon: typeof BookOpen
}

/** 稳定码 → 徽章文案与图标；未知码返回 null（调用方不渲染） */
export function replySourceMeta(source: string): ReplySourceMeta | null {
  switch (source) {
    case 'knowledge_base':
      return { label: '基于知识库回答', Icon: BookOpen }
    case 'data_analysis':
      return { label: '基于业务数据分析', Icon: Database }
    case 'realtime_query':
      return { label: '基于实时数据查询', Icon: Radio }
    case 'system_notice':
      return { label: '系统提示', Icon: Info }
    default:
      return null
  }
}

function ReplyBadge({ source }: { source: string }) {
  const meta = replySourceMeta(source)
  if (!meta) return null
  const { label, Icon } = meta
  return (
    <div className="mt-2 flex items-center gap-1 text-[11px] leading-none text-text-muted"
      data-testid="reply-badge">
      <Icon size={12} aria-hidden />
      <span>{label}</span>
    </div>
  )
}

export default ReplyBadge
