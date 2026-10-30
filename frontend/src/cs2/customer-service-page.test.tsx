// cs-workstation-v2 P1：用户端客服整页渲染冒烟（欢迎态/快捷入口/右栏）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const chatApi = vi.hoisted(() => ({
  streamChat: vi.fn(async function* () {
    /* 空流 */
  }),
  abortChat: vi.fn(async () => {}),
}))
const cs2Api = vi.hoisted(() => ({
  fetchMyConversations: vi.fn(async () => [
    {
      conversation_id: 'conv-a',
      summary: '退款咨询',
      conversation_status: 'active',
      handling_mode: 'ai',
      created_at: '2026-10-31T10:00:00Z',
      last_activity_at: '2026-10-31T10:05:00Z',
      messages: [
        { message_id: '1', sender_type: 'user', content: '我要退款', content_type: 'text', created_at: '2026-10-31T10:00:00Z' },
        { message_id: '2', sender_type: 'assistant', content: '好的，请说明订单号', content_type: 'text', created_at: '2026-10-31T10:00:05Z' },
      ],
    },
  ]),
  fetchMyMessages: vi.fn(async () => ({ conversation_id: 'conv-a', handoff_state: 'none', last_id: 2, messages: [], agent_typing: false, handoff_meta: null })),
  requestHandoff: vi.fn(async () => ({ handoff_state: 'waiting_human' })),
  submitRating: vi.fn(async () => {}),
  fetchCustomerContext: vi.fn(async () => ({
    customer: { display_name: '演示用户（模拟数据）', level: 'gold', register_time: '2026-06-19T00:00:00Z' },
    orders: [
      { id: '1', order_no: 'DEMO-1012', total_amount: 299, status: 'paid', payment_status: 'paid', created_at: null, product_summary: '蓝牙降噪耳机 Pro' },
    ],
    demo_mode: true,
    mock_data: true,
    profile_unavailable: false,
  })),
  newSessionId: vi.fn(() => 'sess-xyz'),
  ORDER_STATUS_ZH: { paid: '待发货' },
}))

vi.mock('@/api/chat', () => chatApi)
vi.mock('./cs2Api', () => cs2Api)

import CustomerServicePage from './CustomerServicePage'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (!mounted) return
  act(() => mounted?.root.unmount())
  mounted.container.remove()
  mounted = null
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<CustomerServicePage />)
  })
  mounted = { container, root }
  return container
}

describe('CustomerServicePage（/customer-service-v2）', () => {
  it('欢迎态：AI 徽章/免责语/演示标记/四个快捷入口/转人工禁用', async () => {
    const c = await mount()
    expect(c.textContent).toContain('AI 智能客服')
    expect(c.textContent).toContain('由人工智能生成')
    expect(c.textContent).toContain('演示沙盒')
    expect(c.querySelectorAll('button').length).toBeGreaterThanOrEqual(4)
    const handoffNew = Array.from(c.querySelectorAll('button')).find((b) => b.textContent?.includes('转接人工'))
    expect(handoffNew?.disabled).toBe(true)
    expect(cs2Api.fetchCustomerContext).toHaveBeenCalled()
  })

  it('会话下拉恢复历史会话并显示消息', async () => {
    const c = await mount()
    const select = c.querySelector('select[aria-label="切换会话"]') as HTMLSelectElement
    expect(select).not.toBeNull()
    await act(async () => {
      select.value = 'conv-a'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(c.textContent).toContain('我要退款')
    expect(c.textContent).toContain('好的，请说明订单号')
  })

  it('历史会话中转人工按钮可用（有消息）', async () => {
    const c = await mount()
    const select = c.querySelector('select[aria-label="切换会话"]') as HTMLSelectElement
    await act(async () => {
      select.value = 'conv-a'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const handoff = Array.from(c.querySelectorAll('button')).find((b) => b.textContent?.trim() === '转人工')
    expect(handoff?.disabled).toBe(false)
    await act(async () => {
      handoff?.click()
    })
    expect(cs2Api.requestHandoff).toHaveBeenCalledWith('conv-a')
  })
})
