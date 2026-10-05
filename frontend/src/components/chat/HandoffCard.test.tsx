/**
 * HandoffCard 组件回归（多域隔离 M3）
 *
 * 浏览器实机走查延后窗口内的组件级兜底：验证三域渲染（图标/按钮文案/
 * 引导话术正文）与跳转意图（travel→/travel 带参、selection→/selection-funnel、
 * cs→sessionStorage+开抽屉事件、埋点上报发出）。
 *
 * 渲染走项目既有 act+createRoot 模式（与 LLMSwitcher.test.tsx 同款）——
 * 不引入 @testing-library/react 新依赖。
 */
import { beforeAll, afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import HandoffCard from './HandoffCard'
import type { HandoffEvent } from '@/types/handoff'

const pushMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn() }),
}))
const silentMock = vi.fn().mockResolvedValue(undefined)
vi.mock('@/lib/fetcher', () => ({
  requestSilent: (...args: unknown[]) => silentMock(...(args as [])),
}))

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(event: HandoffEvent): HTMLElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<HandoffCard event={event} />))
  mounted.push({ container, root })
  return container
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find(
    (b) => (b.textContent || '').includes(text),
  )
  if (!btn) throw new Error(`未找到含「${text}」的按钮`)
  return btn as HTMLButtonElement
}

function evt(partial: Partial<HandoffEvent>): HandoffEvent {
  return {
    v: 1,
    target_domain: 'travel',
    reason: 'domain_planning_request',
    params: {},
    text: '引导话术正文',
    ts: 1700000000,
    ...partial,
  }
}

afterEach(() => {
  pushMock.mockClear()
  silentMock.mockClear()
  sessionStorage.clear()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('HandoffCard — 三域渲染与跳转', () => {
  it('travel：渲染话术与跳转按钮，点击带参跳旅游页并上报埋点', () => {
    const container = mount(evt({
      text: '这是一次行程规划需求',
      params: { destination: '福州', days: 2, party_size: 3, must_go: ['三坊七巷'] },
    }))
    expect(container.textContent).toContain('这是一次行程规划需求')
    expect(container.querySelector('[data-testid="handoff-card"]')).toBeTruthy()
    act(() => { findButton(container, '去旅游规划页').click() })
    expect(pushMock).toHaveBeenCalledWith('/travel?destination=%E7%A6%8F%E5%B7%9E&days=2&party_size=3&must_go=%E4%B8%89%E5%9D%8A%E4%B8%83%E5%B7%B7')
    expect(silentMock).toHaveBeenCalledTimes(1)
  })

  it('selection_funnel：带 category/platform 跳选品页', () => {
    const container = mount(evt({
      target_domain: 'selection_funnel',
      params: { category: '宠物零食', platform: '淘宝' },
    }))
    act(() => { findButton(container, '去智能选品页').click() })
    expect(String(pushMock.mock.calls[0][0])).toContain('/selection-funnel?')
    expect(String(pushMock.mock.calls[0][0])).toContain('category=')
    expect(String(pushMock.mock.calls[0][0])).toContain('platform=')
  })

  it('customer_service：不走路由跳转，写预填并发开抽屉事件', () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const container = mount(evt({
      target_domain: 'customer_service',
      params: { prefill_question: '我的订单怎么退款' },
    }))
    act(() => { findButton(container, '打开智能客服').click() })
    expect(pushMock).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('cs_handoff_prefill')).toBe('我的订单怎么退款')
    expect(dispatchSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'cs-drawer:open' }))
  })

  it('参数为空时 travel 兜底跳裸路由', () => {
    const container = mount(evt({ params: {} }))
    act(() => { findButton(container, '去旅游规划页').click() })
    expect(pushMock).toHaveBeenCalledWith('/travel')
  })
})
