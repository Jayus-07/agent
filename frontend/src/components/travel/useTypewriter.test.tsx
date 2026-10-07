import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTypewriter } from './useTypewriter'

// 与 TravelChatDrawer.test.tsx 同款：告诉 React 处于 act 环境，消除告警
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })

/**
 * 2026-10-07 移动端改造：打字机按「字」推进而非「码元」。
 *
 * 回归点：中文/emoji 一个字符占 2~4 个 UTF-16 码元，旧实现直接
 * slice(0, next) 会切在字符中间，聊天框闪「�」乱码。
 *
 * 不变量（比「不出现乱码」更强）：任何中间态都必须是原文的合法前缀。
 * 只断言无 U+FFFD 是不够的——切错了也可能恰好没产生替换字符。
 */
describe('useTypewriter — 中文字符边界', () => {
  let container: HTMLDivElement
  let root: Root
  let rafQueue: FrameRequestCallback[] = []
  let frameNo = 0
  let nowBase = 1000

  /** 当前 hook 暴露的 shown 值 */
  const readShown = () => container.textContent ?? ''

  const mount = (text: string, enabled: boolean) => {
    function Probe() {
      const shown = useTypewriter(text, enabled)
      return <p>{shown}</p>
    }
    act(() => { root.render(<Probe />) })
  }

  /**
   * 推进一帧。
   * 必须喂「递增的时间戳」：hook 的 tick 用 elapsedFrames = (now-start)/16
   * 算进度，喂恒定时间会让进度恒为 0（动画看似卡住）。返回是否已打完。
   */
  const step = (): boolean => {
    const cbs = rafQueue
    rafQueue = []
    frameNo += 1
    act(() => { cbs.forEach((cb) => cb(nowBase + frameNo * 16)) })
    return rafQueue.length === 0
  }

  beforeEach(() => {
    // 关掉 reduced-motion 兜底，确保走动画分支
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: false, media: q,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
      onchange: null, dispatchEvent: () => false,
    }))
    // 手动驱动 rAF 队列：逐帧断言，而非等动画自己跑完
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      rafQueue.push(cb)
      return rafQueue.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => { rafQueue = [] })
    // 冻结 performance.now：让 effect 里取的 start 与 step() 喂的时间同源，
    // 否则 elapsedFrames = (now - start)/16 会算出巨大值，一帧就跳到结尾，
    // 测试就退化成「只断言最终值」，中间态前缀不变量形同虚设。
    vi.stubGlobal('performance', { now: () => nowBase })

    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    frameNo = 0
    nowBase = 1000
  })

  afterEach(() => {
    act(() => { root.unmount() })
    container.remove()
    rafQueue = []
    vi.unstubAllGlobals()
  })

  it('逐帧中间态始终是原文的合法前缀（不切碎中文、不乱码）', () => {
    const text = '问一句，拿到带证据的答案。行程已按你说的重排，第一天先去西湖。'
    mount(text, true)

    const seen: string[] = []
    for (let i = 0; i < 500 && rafQueue.length > 0; i++) {
      step()
      const frame = readShown()
      expect(frame).not.toContain('\uFFFD')
      // 核心不变量：前缀关系（切在字符中间会直接破坏它）
      expect(text.startsWith(frame)).toBe(true)
      seen.push(frame)
    }
    expect(readShown()).toBe(text)
    expect(seen.length).toBeGreaterThan(1)
  })

  it('含 emoji（代理对）时不切出半个代理', () => {
    const text = '行程已更新 🗺️，第二天安排西湖 ✅ 第三天 free'
    mount(text, true)

    for (let i = 0; i < 500 && rafQueue.length > 0; i++) {
      step()
      expect(readShown()).not.toContain('\uFFFD')
      expect(text.startsWith(readShown())).toBe(true)
    }
    expect(readShown()).toBe(text)
  })

  it('纯 emoji 开头（第一帧就可能切中代理对）', () => {
    const text = '🗺️🧭⛰️ 一份详细行程'
    mount(text, true)

    for (let i = 0; i < 500 && rafQueue.length > 0; i++) {
      step()
      expect(readShown()).not.toContain('\uFFFD')
      expect(text.startsWith(readShown())).toBe(true)
    }
    expect(readShown()).toBe(text)
  })

  it('enabled=false 直显全文，不排任何动画帧', () => {
    mount('短回复直接显示', false)
    expect(readShown()).toBe('短回复直接显示')
    expect(rafQueue.length).toBe(0)
  })

  it('空串安全（不报错、不排帧）', () => {
    mount('', true)
    expect(readShown()).toBe('')
    expect(rafQueue.length).toBe(0)
  })

  it('prefers-reduced-motion 用户直接看全文（无动画）', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: true, media: q,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
      onchange: null, dispatchEvent: () => false,
    }))
    const text = '动画敏感用户应当立刻看到全文'
    mount(text, true)
    expect(readShown()).toBe(text)
    expect(rafQueue.length).toBe(0)
  })
})
