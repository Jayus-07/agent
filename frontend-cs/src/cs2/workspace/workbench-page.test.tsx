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
  // P4 新导出（快捷回复面板 / AI 助手存为话术依赖）
  listCanned: vi.fn(async () => [
    { id: 1, scope: 'personal', owner_agent_id: 'a1', title: '开场问候', content: '您好，很高兴为您服务', category: '通用', enabled: true, updated_at: null },
    { id: 2, scope: 'team', owner_agent_id: null, title: '退款时效', content: '退款 1-3 个工作日到账', category: '售后', enabled: true, updated_at: null },
    { id: 3, scope: 'personal', owner_agent_id: 'a1', title: '停用话术', content: '旧内容', category: '通用', enabled: false, updated_at: null },
  ]),
  createCanned: vi.fn(async (body: { title: string }) => ({
    id: 9, scope: 'personal', title: body.title, content: 'x', category: 'AI', enabled: true,
  })),
  updateCanned: vi.fn(async () => ({ id: 1, scope: 'personal', title: 't', content: 'c', category: '通用', enabled: true })),
  deleteCanned: vi.fn(async () => undefined),
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

  it('P4：快捷回复面板搜索/插入，禁用话术不可点', async () => {
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
    expect(api.listCanned).not.toHaveBeenCalled()

    const cannedBtn = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '快捷回复')
    expect(cannedBtn).toBeDefined()
    await act(async () => {
      cannedBtn?.click()
    })
    await act(async () => {})
    expect(api.listCanned).toHaveBeenCalledTimes(1)
    const panel = c.querySelector('[aria-label="快捷回复面板"]')
    expect(panel).not.toBeNull()
    expect(panel?.textContent).toContain('开场问候')
    expect(panel?.textContent).toContain('退款时效')

    // 禁用行不可点击（disabled）
    const disabledBtn = Array.from(panel!.querySelectorAll('button')).find((b) => b.textContent?.includes('停用话术')) as HTMLButtonElement
    expect(disabledBtn.disabled).toBe(true)

    // 搜索过滤
    const search = panel!.querySelector('input[aria-label="搜索话术"]') as HTMLInputElement
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => {
      setValue.call(search, '退款')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(panel?.textContent).toContain('退款时效')
    expect(panel?.textContent).not.toContain('开场问候')

    // 点击条目插入输入框并关闭面板
    const item = Array.from(panel!.querySelectorAll('button')).find((b) => b.textContent?.includes('退款时效'))
    await act(async () => {
      item?.click()
    })
    await act(async () => {})
    const textarea = c.querySelector('textarea[aria-label="回复输入框"]') as HTMLTextAreaElement
    expect(textarea.value).toBe('退款 1-3 个工作日到账')
    expect(c.querySelector('[aria-label="快捷回复面板"]')).toBeNull()
  })

  it('P4：AI 建议按钮切到右栏 AI 助手 Tab', async () => {
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
    const aiBtn = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === 'AI 建议')
    await act(async () => {
      aiBtn?.click()
    })
    expect(c.textContent).toContain('AI 实时辅助 · 监听中')
    expect(c.textContent).toContain('意图识别')
    expect(c.textContent).toContain('AI 正在分析最新消息')
  })
})
