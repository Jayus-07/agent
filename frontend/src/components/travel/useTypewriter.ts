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
 *
 * 2026-10-07 移动端改造：进度以「码点」计，不是「码元」。
 * JS 字符串是 UTF-16——emoji 代理对占 2 码元、带变体选择符（U+FE0F）的
 * 组合可到 4 码元。旧实现直接 slice(0, next) 会切在字符中间，
 * 聊天框里闪「�」乱码（中文 BMP 汉字是 1 码元，反而不会碎，所以
 * 这个坑只在带 emoji 的回复上暴露，很容易漏测）。
 *
 * 解法：预先按码点展开成数组并建「累计码元数」断点表，逐帧查表取前缀。
 * 单调递增、不回退——比「切完再回退到字符边界」更稳，后者遇到宽字符会
 * 反复被拽回同一点，视觉上像卡住不前进。
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
    if (shown === text) {
      setShown(text)
      return
    }

    // 按码点展开 + 建断点表：boundaries[k] = 前 k+1 个码点占用的码元总数
    const codePoints = Array.from(text)
    const boundaries: number[] = []
    let acc = 0
    for (const cp of codePoints) {
      acc += cp.length
      boundaries.push(acc)
    }
    const total = boundaries.length

    // 续打起点：已显示内容是原文前缀时，从对应码点数继续；否则从头
    let fromPoints = 0
    if (text.startsWith(shown) && shown.length > 0) {
      // 二分找第一个「累计码元数 >= 已显示码元数」的码点位置
      let lo = 0
      let hi = total
      while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (boundaries[mid] < shown.length) lo = mid + 1
        else hi = mid
      }
      fromPoints = lo
    }
    if (fromPoints >= total) {
      setShown(text)
      return
    }

    const start = performance.now()
    // 每帧步进：保证整条 ~0.3~2s 内打完（长文不拖沓）
    const perFrame = Math.max(1, Math.ceil(total / 120))
    const tick = (now: number) => {
      const elapsedFrames = Math.floor((now - start) / 16)
      const next = Math.min(total, fromPoints + elapsedFrames * perFrame)
      setShown(codePoints.slice(0, next).join(''))
      if (next < total) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
    // shown 不进 deps：动画循环节里自己 setState，不因每帧重挂 effect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, enabled])

  return shown
}
