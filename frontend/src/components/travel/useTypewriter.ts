'use client'

import { useEffect, useRef, useState } from 'react'

/**
 * useTypewriter — AI 回复打字机渐显（M2，前端伪装流式）。
 *
 * 口径（2026-10-03 拍板）：
 * - 只对 AI 文本气泡生效；历史消息/草案卡/结果卡直显（enabled=false 即全量）。
 * - 速度自适应：长文本按比例加速（整条最多 ~2s），短文本约 12~16ms/字。
 * - prefers-reduced-motion 用户直接显示全文，无动画。
 * - enabled 从 true→false（消息不再是最新条）时立即补全，防残句。
 */
export function useTypewriter(text: string, enabled: boolean): string {
  const [shown, setShown] = useState(() => (enabled ? '' : text))
  const rafRef = useRef(0)

  useEffect(() => {
    if (!enabled) {
      setShown(text)
      return
    }
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setShown(text)
      return
    }
    // 已显示部分仍是前缀（组件复用/文本未变）则续打，否则从头
    const from = text.startsWith(shown) ? shown.length : 0
    if (from >= text.length) {
      setShown(text)
      return
    }
    const start = performance.now()
    // 每帧步进：保证整条 ~0.3~2s 内打完（长文不拖沓）
    const perFrame = Math.max(1, Math.ceil(text.length / 120))
    const tick = (now: number) => {
      const elapsedFrames = Math.floor((now - start) / 16)
      const next = Math.min(text.length, from + elapsedFrames * perFrame)
      setShown(text.slice(0, next))
      if (next < text.length) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
    // shown 不进 deps：动画循环节里自己 setState，不因每帧重挂 effect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, enabled])

  return shown
}
