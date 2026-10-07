'use client'

/**
 * StreamingContent 匀速打字机回归（2026-10-08）。
 *
 * 实机确诊：LLM 短回答 280ms 内全部到达，直通渲染=「一下子全出现」。
 * 锁定行为：已到达内容按 ~45 字/s 匀速放字，不一次性直通。
 *
 * 与 useTypewriter.test.tsx 同款：createRoot + 手动 rAF 队列（项目无
 * testing-library，jsdom 下 rAF 需自管）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StreamingContent from '@/components/chat/StreamingContent'

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

const LONG_TEXT = '你好，我是企业智能助手，能为你提供旅游行程规划、订单与售后客服、商品智能选品、经营数据查询与分析，还有知识库问答哦。'

describe('StreamingContent 匀速打字机', () => {
  let container: HTMLDivElement
  let root: Root
  let rafQueue: FrameRequestCallback[] = []
  let nowBase = 1000

  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      rafQueue.push(cb)
      return rafQueue.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  })

  /** 推进 n 帧 ×16ms（act 内：flush 每帧的 setState） */
  const advance = (frames: number) => {
    act(() => {
      for (let i = 0; i < frames; i++) {
        const queue = rafQueue
        rafQueue = []
        nowBase += 16
        for (const cb of queue) cb(nowBase)
      }
    })
  }

  const mount = (useDelta: () => string) => {
    act(() => root.render(<StreamingContent useDeltaText={useDelta} />))
  }

  it('长文本不直通渲染：早期只显示部分字符', () => {
    let text = ''
    const useDelta = () => text
    mount(useDelta)
    act(() => {
      text = LONG_TEXT
      root.render(<StreamingContent useDeltaText={useDelta} />)
    })
    advance(3) // ~48ms：45 字/s 只应放出 ~2-3 字
    const shown = container.textContent || ''
    expect(shown.length).toBeLessThan(10)
    expect(shown.length).toBeGreaterThan(0)
    expect(shown).not.toContain('知识库问答')
  })

  it('时间推进后逐步追平全文', () => {
    let text = ''
    const useDelta = () => text
    mount(useDelta)
    act(() => {
      text = LONG_TEXT
      root.render(<StreamingContent useDeltaText={useDelta} />)
    })
    advance(300) // 4.8s 足够 45 字/s 演完 62 字
    expect(container.textContent).toContain(LONG_TEXT.slice(-6))
  })

  it('新一轮回答（文本变短）不残留上一轮内容', () => {
    let text = LONG_TEXT
    const useDelta = () => text
    mount(useDelta)
    advance(300)
    act(() => {
      text = '你好'
      root.render(<StreamingContent useDeltaText={useDelta} />)
    })
    advance(60)
    const body = container.textContent || ''
    expect(body).not.toContain('知识库问答')
    expect(body).toContain('你好')
  })
})
