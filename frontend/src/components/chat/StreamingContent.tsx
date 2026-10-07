'use client'

/**
 * 流式内容渲染 — chat / csChat 两个聊天页共享。
 *
 * 此前只有主 chat 有 StreamingBubble，CS 页漂移成"转圈→整段出现"。
 * useDeltaText 由调用方绑定具体 store（(() => useXxxStore(s => s.deltaText))），
 * 组件只依赖 deltaText 字段，与 store 实现解耦。
 *
 * 匀速打字机（2026-10-08）：LLM 吐字速度不均——短回答 0.3s 内全部到达，
 * 直通渲染肉眼看就是「一下子全出现」（实机 SSE 抓包确诊：44 字/280ms）。
 * 现在维护 target（已到达全量）与 displayed（平滑推进的可见长度），按
 * BASE_CPS 匀速放字；LLM 慢时有多少放多少不拖慢（displayed 跟不上 target
 * 时 gap 大于一段阈值自动加速追赶），流静止（deltaText 不再增长）且演完
 * 即停表。Markdown 按可见前缀解析——半截标记由 MarkdownContent 容错。
 */
import { useEffect, useRef, useState } from 'react'
import MarkdownContent from '@/components/chat/MarkdownContent'

/** 基础放字速度（字符/秒）：45 字/s ≈ 舒适阅读的打字机观感 */
const BASE_CPS = 45
/** 可见落后于到达超过该字符数时线性加速（防长回答越落越远） */
const CATCHUP_GAP = 120
/** 追加速度上限 */
const MAX_CPS = 400

/** 独立光标组件 —— 父级 re-render 不会中断 CSS 动画 */
function StreamingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-4 bg-accent ml-0.5 align-text-bottom rounded-full cursor-blink"
      aria-hidden
    />
  )
}

export default function StreamingContent({
  useDeltaText,
  hideDots = false,
}: {
  useDeltaText: () => string
  /** 无正文时不再渲染加载点（调用方在上方有自己的状态行时使用） */
  hideDots?: boolean
}) {
  const deltaText = useDeltaText()
  // displayed = 匀速推进的可见前缀长度；target = 已到达长度
  const [displayedLen, setDisplayedLen] = useState(0)
  const targetRef = useRef(0)
  const displayedRef = useRef(0)
  const rafRef = useRef<number | null>(null)
  const lastTsRef = useRef<number | null>(null)

  // 新一轮回答（target 变短/清零）时重置推进器。render 期只动 ref 不
  // setState；长度 state 由重启的 rAF 第一帧对齐。
  if (deltaText.length < targetRef.current) {
    targetRef.current = deltaText.length
    displayedRef.current = Math.min(displayedRef.current, deltaText.length)
  }
  targetRef.current = Math.max(targetRef.current, deltaText.length)

  useEffect(() => {
    if (rafRef.current !== null) return
    // 重启对齐：ref 已在 render 期调整而 state 滞后
    setDisplayedLen(displayedRef.current)
    const step = (ts: number) => {
      const target = targetRef.current
      const shown = displayedRef.current
      if (lastTsRef.current === null) lastTsRef.current = ts
      const dt = Math.min((ts - lastTsRef.current) / 1000, 0.2)
      lastTsRef.current = ts

      if (shown < target) {
        const gap = target - shown
        const cps = gap > CATCHUP_GAP
          ? Math.min(MAX_CPS, BASE_CPS + gap * 8)
          : BASE_CPS
        displayedRef.current = Math.min(target, shown + cps * dt)
        setDisplayedLen(displayedRef.current)
      }
      // 静止且演完 → 停表（下一帧 deltaText 变化时重启）
      if (displayedRef.current >= targetRef.current) {
        rafRef.current = null
        lastTsRef.current = null
        return
      }
      rafRef.current = requestAnimationFrame(step)
    }
    rafRef.current = requestAnimationFrame(step)
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
        lastTsRef.current = null
      }
    }
  }, [deltaText])

  // 可见前缀 = 已到达全量的前 displayedLen 字符
  const displayContent = deltaText.slice(0, displayedLen)
  if (!displayContent && hideDots) return null
  return (
    <div className="text-[15px] leading-[1.6] text-[#333]">
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
