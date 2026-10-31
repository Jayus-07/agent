// cs-workstation-v2 P1：坐席工作台交互（队列/认领/消息渲染/发送/关闭 + WS 断连降级提示）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({
  getHandoffQueue: vi.fn(async () => ({
    items: [
      {
        conversation_id: 'conv-q1', user_id: 'user-abc12345', handoff_state: 'waiting_human',
        handoff_id: 'h1', trigger_type: 'manual', trigger_reason: null,
        updated_at: new Date(Date.now() - 84_000).toISOString(), last_message_preview: '我要退款',
      },
    ],
    total: 1,
  })),
  getConversationDetail: vi.fn(async () => ({
    conversation_id: 'conv-q1', user_id: 'user-abc12345', conversation_status: 'active',
    handling_mode: 'agent', priority: 'medium', channel: 'web', summary: '退款咨询',
    context_summary: '用户咨询 DEMO-1012 退款', trace_count: 2, last_trace_id: 'tr-1',
    created_at: '2026-10-31T10:00:00Z', last_activity_at: '2026-10-31T10:05:00Z',
    messages: [],
  })),
  getHandoffMessages: vi.fn(async () => ({
    conversation_id: 'conv-q1', handoff_state: 'waiting_human', last_id: 3,
    messages: [
      { message_id: '2', sender_type: 'user', content: '我要退款', content_type: 'text', created_at: '2026-10-31T10:01:00Z' },
      { message_id: '3', sender_type: 'assistant', content: '好的，请说明订单号', content_type: 'text', created_at: '2026-10-31T10:01:05Z' },
    ],
    agent_typing: false, handoff_meta: null,
  })),
  getConversationEvents: vi.fn(async () => ({ conversation_id: 'conv-q1', after_seq: 0, events: [] })),
  claimConversation: vi.fn(async () => ({ conversation_id: 'conv-q1', handoff_state: 'human_active', agent_id: 'a1', already_claimed: false })),
  closeConversation: vi.fn(async () => ({ conversation_id: 'conv-q1', handoff_state: 'closed', closed_by: 'a1' })),
  sendAgentMessage: vi.fn(async (_cid: string, content: string) => ({
    message_id: '9', sender_type: 'human_agent', content, created_at: '2026-10-31T10:06:00Z',
  })),
  issueWsTicket: vi.fn(async () => ({ ticket: 't', ws_path: '/ws/cs/agent', ttl: 60 })),
  getPendingProposal: vi.fn(async () => null),
  getConversationCustomerContext: vi.fn(async () => ({
    customer: { display_name: '演示用户' }, orders: [], balance: null,
  })),
  // P3 新导出（顶栏状态/通知与 offer 接单条依赖）
  getMyStatus: vi.fn(async () => ({
    agent_id: 'a1', status: 'online', accepting: true, presence_online: true,
  })),
  updateMyStatus: vi.fn(async (status: string) => ({
    agent_id: 'a1', status, accepting: true, presence_online: true,
  })),
  listMyOffers: vi.fn(async () => []),
  acceptOffer: vi.fn(async () => ({ conversation_id: 'conv-q1', handoff_state: 'human_active' })),
  declineOffer: vi.fn(async () => ({ conversation_id: 'conv-q1', handoff_state: 'waiting_human' })),
  transferConversation: vi.fn(async () => ({
    conversation_id: 'conv-q1', handoff_state: 'agent_offered',
    from_agent_id: 'a1', to_agent_id: 'a2',
  })),
}))

vi.mock('./api', () => api)
vi.mock('./useAgentSocket', () => ({
  useAgentSocket: () => ({ connected: true, lastSeq: 0 }),
}))

import WorkbenchPage from './WorkbenchPage'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<WorkbenchPage />)
  })
  mounted = { container, root }
  return container
}

describe('WorkbenchPage（/cs2/workspace）', () => {
  it('队列 Tab 展示待接入项与认领按钮', async () => {
    const c = await mount()
    const queueTab = Array.from(c.querySelectorAll('[role="tab"]')).find((b) => b.textContent?.includes('待接入'))
    await act(async () => {
      queueTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(c.textContent).toContain('用户 user-abc')
    expect(c.textContent).toContain('我要退款')
    const claim = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '认领')
    expect(claim).toBeDefined()
  })

  it('认领后进入我的会话并加载消息流', async () => {
    const c = await mount()
    const queueTab = Array.from(c.querySelectorAll('[role="tab"]')).find((b) => b.textContent?.includes('待接入'))
    await act(async () => {
      queueTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const claim = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '认领')
    await act(async () => {
      claim?.click()
    })
    await act(async () => {})
    expect(api.claimConversation).toHaveBeenCalledWith('conv-q1')
    expect(c.textContent).toContain('好的，请说明订单号')
    expect(c.textContent).toContain('退款咨询')
    expect(c.textContent).toContain('AI 阶段摘要')
    expect(c.textContent).toContain('用户咨询 DEMO-1012 退款')
  })

  it('发送回复调用 agent-messages 且消息立即上屏', async () => {
    const c = await mount()
    const queueTab = Array.from(c.querySelectorAll('[role="tab"]')).find((b) => b.textContent?.includes('待接入'))
    await act(async () => {
      queueTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const claim = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '认领')
    await act(async () => {
      claim?.click()
    })
    await act(async () => {})
    const textarea = c.querySelector('textarea[aria-label="回复输入框"]') as HTMLTextAreaElement
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setValue.call(textarea, '您好，已收到您的退款诉求')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const send = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '发送')
    await act(async () => {
      send?.click()
    })
    expect(api.sendAgentMessage).toHaveBeenCalledWith('conv-q1', '您好，已收到您的退款诉求')
    expect(c.textContent).toContain('坐席 · 我')
  })

  it('关闭会话后输入区禁用', async () => {
    const c = await mount()
    const queueTab = Array.from(c.querySelectorAll('[role="tab"]')).find((b) => b.textContent?.includes('待接入'))
    await act(async () => {
      queueTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    const claim = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '认领')
    await act(async () => {
      claim?.click()
    })
    await act(async () => {})
    const closeBtn = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '关闭会话')
    await act(async () => {
      closeBtn?.click()
    })
    expect(api.closeConversation).toHaveBeenCalledWith('conv-q1')
    const textarea = c.querySelector('textarea[aria-label="回复输入框"]') as HTMLTextAreaElement
    expect(textarea.disabled).toBe(true)
  })
})
