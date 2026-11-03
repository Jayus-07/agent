'use client'

/**
 * TravelMessageMarkdown — 旅行助手消息正文渲染（对齐原型 C3）。
 *
 * 复用主聊天 MarkdownContent（react-markdown + gfm + breaks + highlight），
 * 不引入新依赖。LLM 回复带 markdown 源码（标题、加粗、列表符号），此前
 * 裸奔显示；这里在作用域内收排版：13px 正文继承气泡文字色，标题/段落间
 * 距压缩到气泡尺度（markdown-body 全局样式按主聊天 15px 设计，直接用会
 * 撑破气泡）。
 */
import MarkdownContent from '@/components/chat/MarkdownContent'

export default function TravelMessageMarkdown({ content }: { content: string }) {
  // 打字机起帧为空串：静默渲染，避免 MarkdownContent 的「(无内容)」占位闪现
  if (!content) return null
  return (
    <div className="[&_.markdown-body]:text-[13px] [&_.markdown-body]:leading-[1.65] [&_.markdown-body]:text-inherit [&_.markdown-body_p]:my-1 [&_.markdown-body_h1]:mb-1 [&_.markdown-body_h1]:mt-2.5 [&_.markdown-body_h2]:mb-1 [&_.markdown-body_h2]:mt-2.5 [&_.markdown-body_h3]:mb-1 [&_.markdown-body_h3]:mt-2 [&_.markdown-body_li]:my-0.5 [&_.markdown-body_ol]:my-1 [&_.markdown-body_ul]:my-1">
      <MarkdownContent content={content} />
    </div>
  )
}
