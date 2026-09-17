'use client'

/**
 * MessageBubble — 单条消息气泡（DeepSeek 风格：用户浅色气泡右对齐，AI 无背景左对齐）
 *
 * 设计原则（P0-4 / P1-8 / P1-11）：
 *   1. 普通气泡不订阅流式字段：仅当 isLast && isLoading && !content 时进入"流式模式"，
 *      流式文本由共享的 StreamingContent 组件订阅 store.deltaText，
 *      思考链由 ThinkingPanel 订阅 store.thinkingText —— 都是组件级订阅。
 *      流式期间 store 更新只让最后一个 bubble 重渲；N-1 条历史气泡由 React.memo 跳过。
 *   2. 用户气泡始终静态 → 不订阅任何 store 字段。
 */
import { memo } from 'react'
import type { Message } from '@/lib/types'
import { useChatStore } from '@/store/chat'
import { useSSE } from '@/hooks/useSSE'
import SourceCard from './SourceCard'
import MarkdownContent from './MarkdownContent'
import MessageActions from './MessageActions'
import SqlViz from './SqlViz'
import TokenInfo from './TokenInfo'
import StreamingContent from './StreamingContent'
import ThinkingPanel from './ThinkingPanel'
import CompletionLine from './CompletionLine'

function stripReferences(content: string): string {
  const markers = ['\n\n---\n\n### 参考文献', '\n\n---\n\n### 参考来源',
                   '\n\n### 参考文献', '\n\n### 参考来源']
  for (const marker of markers) {
    const idx = content.indexOf(marker)
    if (idx !== -1) return content.slice(0, idx)
  }
  return content
}

/** 绑定主 chat store 的流式文本订阅（StreamingContent 经 props 接收 hook） */
function useChatDelta(): string {
  return useChatStore((s) => s.deltaText)
}

/** 绑定主 chat store 的思考链订阅（ThinkingPanel 经 props 接收 hook） */
function useChatThinking(): { text: string; seconds: number | null } {
  const text = useChatStore((s) => s.thinkingText)
  const seconds = useChatStore((s) => s.thinkingSeconds)
  return { text, seconds }
}

/**
 * 流式阶段不再渲染独立状态行 —— 状态提示统一由消息流内的 ProgressCards
 * 「{当前节点} · 已消耗 N tokens [停止生成]」一行承载，避免同一屏出现
 * 「生成回复中」「思考中」两条互相独立的状态（2026-09-17 修复）。
 */

interface MessageBubbleProps {
  message: Message
  isLast: boolean
  sessionId?: string
  question?: string
}

function MessageBubbleImpl({ message, isLast, sessionId, question }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  // 只订阅 isLoading —— 单字段、引用稳定；流式文本/思考链由子组件单独订阅
  const isLoading = useChatStore((s) => s.isLoading)
  const { regenerate } = useSSE()
  // 流式模式：最后一条 assistant + 加载中 + 当前消息还没写入完成内容
  const isCurrentStreaming = !isUser && isLoading && isLast && !message.content

  return (
    <div className={`animate-fade-in group/message ${isUser ? 'flex justify-end' : ''}`}>
      <div className={`min-w-0 ${isUser ? 'max-w-[75%]' : 'w-full'}`}>
        {isUser ? (
          <div>
            <div className="bg-surface-elevated border border-border-subtle text-text-primary rounded-xl px-4 py-2.5
              text-[15px] leading-[1.6] whitespace-pre-wrap break-words">
              {message.content}
            </div>
            <MessageActions
              content={message.content || ''}
              isUser
              isLast={isLast}
              sessionId={sessionId}
              msgId={message.id}
            />
          </div>
        ) : (
          <div>
            {isCurrentStreaming ? (
              <div>
                <StreamingThinking />
                <StreamingContent useDeltaText={useChatDelta} hideDots />
              </div>
            ) : (
              <>
                {message.trace && <CompletionLine trace={message.trace} />}
                {message.thinking && (
                  <ThinkingPanel
                    text={message.thinking}
                    seconds={message.thinkingSeconds ?? null}
                  />
                )}
                <div className="text-[15px] leading-[1.6] text-[#333]">
                  <MarkdownContent
                    content={
                      message.sources && message.sources.length > 0
                        ? stripReferences(message.content)
                        : message.content
                    }
                  />
                </div>
                {/* 参考来源置于正文之下（浅色卡片） */}
                {message.sources && message.sources.length > 0 && (
                  <SourceCard sources={message.sources} />
                )}
              </>
            )}

            {/* 流式结束后才显示附属信息（SqlViz/TokenInfo/Actions） */}
            {!isCurrentStreaming && message.content && (
              <>
                <SqlViz streamEvents={message.streamEvents ?? []} />
                <TokenInfo streamEvents={message.streamEvents ?? []} usage={message.usage} />
                <MessageActions
                  content={message.content}
                  isUser={false}
                  isLast={isLast}
                  sessionId={sessionId}
                  msgId={message.id}
                  question={question}
                  onRegenerate={sessionId ? () => regenerate(sessionId) : undefined}
                />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/** 流式中的思考链面板：订阅 store，思考中展开、首块回答到达自动收起 */
function StreamingThinking() {
  const { text, seconds } = useChatThinking()
  const answering = useChatStore((s) => s.deltaText !== '')
  return <ThinkingPanel text={text} seconds={seconds} live answering={answering} />
}

// memo：message / isLast prop 不变则跳过 re-render ——
// store 在流式期间 sessions 引用变，会让 MessageList 重渲染，但只要 message 引用稳定，
// N-1 条历史气泡就被 memo 拦截，避免 MarkdownContent 重复解析整篇。
const MessageBubble = memo(MessageBubbleImpl, (prev, next) =>
  prev.isLast === next.isLast && prev.message === next.message,
)

export default MessageBubble
