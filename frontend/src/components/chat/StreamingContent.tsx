'use client'

/**
 * 流式内容渲染 — chat / csChat 两个聊天页共享。
 *
 * 此前只有主 chat 有 StreamingBubble，CS 页漂移成"转圈→整段出现"。
 * useDeltaText 由调用方绑定具体 store（(() => useXxxStore(s => s.deltaText))），
 * 组件只依赖 deltaText 字段，与 store 实现解耦。
 *
 * rAF 节流：一帧内到达的多条 delta 只触发一次 Markdown 解析；
 * 光标抽成独立组件，父级 re-render 不中断 CSS 动画。
 */
import { useEffect, useRef, useState } from 'react'
import MarkdownContent from '@/components/MarkdownContent'

/** 独立光标组件 —— 父级 re-render 不会中断 CSS 动画 */
function StreamingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-4 bg-accent ml-0.5 align-text-bottom rounded-full cursor-blink"
      aria-hidden
    />
  )
}

export default function StreamingContent({ useDeltaText }: { useDeltaText: () => string }) {
  const deltaText = useDeltaText()
  const [renderText, setRenderText] = useState('')
  const rafRef = useRef<number | null>(null)
  const lastRenderedRef = useRef('')

  useEffect(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      if (deltaText !== lastRenderedRef.current) {
        lastRenderedRef.current = deltaText
        setRenderText(deltaText)
      }
    })
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [deltaText])

  const displayContent = renderText || deltaText
  return (
    <div className="text-sm text-text-primary leading-relaxed">
      {displayContent ? (
        <>
          <MarkdownContent content={displayContent} />
          <StreamingCursor />
        </>
      ) : (
        <div className="flex items-center gap-1.5 py-2">
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
          <span className="typing-dot w-1.5 h-1.5 rounded-full bg-accent/30 inline-block" />
        </div>
      )}
    </div>
  )
}
