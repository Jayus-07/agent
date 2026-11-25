// cs-workstation-v2 P1：用户端客服整页渲染冒烟（欢迎态/快捷入口/右栏）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const chatApi = vi.hoisted(() => ({
  streamChat: vi.fn(async function* (_req?: unknown) {
    /* 空流 */
  }),
  abortChat: vi.fn(async () => {}),
}))
const cs2Api = vi.hoisted(() => ({
  fetchMyConversations: vi.fn(async (): Promise<
    Array<{
      conversation_id: string
      summary?: string | null
      conversation_status: string
      latest_handoff_closed?: boolean
      handling_mode: string
      created_at: string
      last_activity_at?: string | null
      messages?: Array<{
        message_id: string
        sender_type: string
        content: string
        content_type: string
        attachments?: Array<{ type: string; order_no?: string }> | null
        created_at: string
      }>
    }>
  > => [
    {
      conversation_id: 'conv-a',
      summary: '退款咨询',
      conversation_status: 'active',
      handling_mode: 'ai',
      created_at: '2026-10-31T10:00:00Z',
      last_activity_at: '2026-10-31T10:05:00Z',
      messages: [
        { message_id: '1', sender_type: 'user', content: '我要退款', content_type: 'text', attachments: null, created_at: '2026-10-31T10:00:00Z' },
        { message_id: '2', sender_type: 'assistant', content: '好的，请说明订单号', content_type: 'text', attachments: null, created_at: '2026-10-31T10:00:05Z' },
      ],
    },
  ]),
  fetchMyMessages: vi.fn(async (): Promise<{
    conversation_id: string
    handoff_state: string
    last_id: number
    messages: Array<{ message_id: string; sender_type: string; content: string; content_type: string; attachments?: Array<{ type: string; order_no?: string }> | null; created_at: string }>
    agent_typing: boolean
    handoff_meta?: { handoff_id: string; handoff_state: string; total_deadline_at: string | null; queue_position: number } | null
  }> => ({
    conversation_id: 'conv-a',
    handoff_state: 'none',
    last_id: 2,
    messages: [],
    agent_typing: false,
    handoff_meta: null,
  })),
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
  fetchPendingSnapshot: vi.fn<(cid: string) => Promise<unknown>>(async () => null),
  confirmAction: vi.fn(async () => ({ status: 'success', answer: '' })),
  updateRefundReason: vi.fn(async () => {}),
  listRefundRequests: vi.fn(async () => []),
  listMyTickets: vi.fn(async (_limit: number, _offset: number): Promise<{
    items: Array<{ ticket_id: string; title: string }>
    total: number
  }> => ({ items: [], total: 0 })),
}))

const mallApi = vi.hoisted(() => ({
  listMyOrders: vi.fn(async () => [
    { order_no: 'MO-AAA111', total_amount: 299, status: 'shipped', payment_status: 'paid', created_at: '2026-11-20T09:06:00Z', product_summary: '蓝牙降噪耳机 Pro' },
    { order_no: 'MO-BBB222', total_amount: 1299, status: 'paid', payment_status: 'paid', created_at: '2026-11-14T08:00:00Z', product_summary: '机械键盘 87 键' },
  ]),
  ORDER_STATUS_ZH: { pending: '待付款', paid: '待发货', shipped: '已发货' },
  money: (v: string | number | null | undefined) => `¥${Number(v ?? 0).toFixed(2)}`,
  topUp: vi.fn(async () => ({ balance: 1000, currency: 'CNY', ledger: [] })),
}))
vi.mock('@/api/chat', () => chatApi)
vi.mock('./cs2Api', () => cs2Api)
vi.mock('@/mall/api', () => mallApi)

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
  window.sessionStorage.clear()
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

// 会话切换已收进头像抽屉（顶栏不再有下拉）：点头像 → 点抽屉里的会话项
async function openConversation(c: HTMLElement, label: string) {
  const avatar = c.querySelector('button[aria-label="打开个人中心"]')
  expect(avatar).not.toBeNull()
  await act(async () => {
    avatar?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
  const item = Array.from(c.querySelectorAll('aside[aria-label="个人中心"] button')).find(
    (b) => b.textContent?.includes(label),
  )
  expect(item).not.toBeNull()
  await act(async () => {
    item?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

describe('CustomerServicePage（/customer-service，P8 切换）', () => {
  it('欢迎态：开场白气泡/演示标记/快捷入口常驻输入栏上方/转人工禁用', async () => {
    const c = await mount()
    // 开场白改为淘宝式问候气泡，AI 免责由每条气泡的标签承载
    expect(c.textContent).toContain('很高兴为您服务')
    expect(c.textContent).toContain('由人工智能生成')
    expect(c.textContent).toContain('演示环境 · 数据为示例')
    // 快捷入口常驻输入栏上方（aria 容器），不再只在欢迎卡出现
    const quickBar = c.querySelector('[aria-label="快捷入口"]')
    expect(quickBar).not.toBeNull()
    expect(quickBar?.textContent).toContain('查询订单')
    expect(quickBar?.textContent).toContain('物流追踪')
    expect(quickBar?.textContent).toContain('逛商城')
    // 转人工入口已移至输入栏（aria-label 图标按钮），新会话态禁用
    const handoffNew = c.querySelector<HTMLButtonElement>('button[aria-label="转人工"]')
    expect(handoffNew).not.toBeNull()
    expect(handoffNew?.disabled).toBe(true)
    expect(cs2Api.fetchCustomerContext).toHaveBeenCalled()
  })

  it('点击快捷入口 chip 直接发送对应话术', async () => {
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (b) => b.textContent?.includes('查询订单'),
    )
    expect(chip).not.toBeNull()
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    // 用户消息入列（开场白气泡随之让位给真实消息流）
    expect(c.textContent).toContain('帮我查一下我的订单状态')
    expect(c.textContent).not.toContain('很高兴为您服务')
  })

  it('SSE 断流导致空回复时展示友好重试卡而非「（空）」占位，重试重发上一条话术', async () => {
    // error 事件路径由 reducer 兜底为系统提示；这里模拟的是流直接抛异常（断网/网关 503）
    chatApi.streamChat.mockImplementationOnce(async function* () {
      throw new Error('tunnel reset')
    })
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (b) => b.textContent?.includes('查询订单'),
    )
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    expect(c.textContent).not.toContain('（空）')
    expect(c.textContent).toContain('刚才的回复没有生成成功')
    const retry = Array.from(c.querySelectorAll('button')).find((b) => b.textContent?.includes('重试'))
    expect(retry).not.toBeNull()
    chatApi.streamChat.mockClear()
    await act(async () => {
      retry?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    // 重试 = 用上一条用户话术重新发起一轮 SSE
    expect(chatApi.streamChat).toHaveBeenCalledTimes(1)
    expect(chatApi.streamChat.mock.calls[0]?.[0]).toMatchObject({
      question: '帮我查一下我的订单状态',
    })
  })

  it('首个字出现前停止时显示已停止，并能重试上一条消息', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    chatApi.streamChat.mockImplementationOnce(async function* () {
      await pending
    })
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (button) => button.textContent?.includes('查询订单'),
    )
    await act(async () => chip?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const stop = c.querySelector<HTMLButtonElement>('button[aria-label="停止生成"]')
    expect(stop).not.toBeNull()
    await act(async () => {
      stop?.click()
      finish()
    })
    expect(c.textContent).toContain('已停止')
    const retry = Array.from(c.querySelectorAll('button')).find(
      (button) => button.textContent === '重试',
    )
    expect(retry).toBeDefined()
    await act(async () => retry?.click())
    expect(chatApi.streamChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ question: '帮我查一下我的订单状态' }),
      expect.any(AbortSignal),
      'customer-service',
    )
  })

  it('会话下拉恢复历史会话并显示消息', async () => {
    const c = await mount()
    await openConversation(c, '退款咨询')
    expect(c.textContent).toContain('我要退款')
    expect(c.textContent).toContain('好的，请说明订单号')
  })

  it('历史会话中转人工按钮可用（有消息）', async () => {
    const c = await mount()
    await openConversation(c, '退款咨询')
    const handoff = c.querySelector<HTMLButtonElement>('button[aria-label="转人工"]')
    expect(handoff?.disabled).toBe(false)
    await act(async () => {
      handoff?.click()
    })
    expect(cs2Api.requestHandoff).toHaveBeenCalledWith('conv-a')
  })

  it('need_info 提案（confirmable=false）不渲染确认按钮，只提示补充信息', async () => {
    cs2Api.fetchPendingSnapshot.mockResolvedValueOnce({
      proposal_id: 'p-need-info',
      version: 1,
      action_type: 'refund_request',
      masked_target: '订单',
      summary: '提交退款申请',
      reason: '',
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      state: 'pending',
      confirmable: false,
    })
    const c = await mount()
    await openConversation(c, '退款咨询')
    await act(async () => {})
    expect(cs2Api.fetchPendingSnapshot).toHaveBeenCalledWith('conv-a')
    expect(c.textContent).toContain('待确认信息尚未补全')
    expect(Array.from(c.querySelectorAll('button')).some((b) => b.textContent?.includes('确认退款'))).toBe(false)
  })

  it('沙箱退款确认卡说明只更新审核记录，钱包余额不变', async () => {
    cs2Api.fetchPendingSnapshot.mockResolvedValueOnce({
      proposal_id: 'p-sandbox', version: 1, action_type: 'refund_request',
      masked_target: '订单', summary: '提交退款申请', reason: '专用验收',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      state: 'pending', confirmable: true, refund_review_mode: 'sandbox',
    })
    const c = await mount()
    await openConversation(c, '退款咨询')
    expect(c.textContent).toContain('审核只更新申请记录，钱包余额不变')
    expect(c.textContent).not.toContain('货款自动退回账户余额')
  })

  it('「发订单给客服」chip 打开订单抽屉，点发送带结构化文本走既有通道', async () => {
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (b) => b.textContent?.includes('发订单给客服'),
    )
    expect(chip).not.toBeNull()
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    const sheet = c.querySelector('[aria-label="订单选择"]')
    expect(sheet).not.toBeNull()
    expect(mallApi.listMyOrders).toHaveBeenCalledWith(20)
    expect(sheet?.textContent).toContain('MO-AAA111')
    expect(sheet?.textContent).toContain('已发货')
    const sendBtn = Array.from(sheet?.querySelectorAll('button') ?? []).find(
      (b) => b.textContent === '发送',
    )
    expect(sendBtn).not.toBeNull()
    await act(async () => {
      sendBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    expect(c.textContent).toContain('【订单咨询】订单号 MO-AAA111')
    expect(c.querySelector('[aria-label="订单选择"]')).toBeNull()
  })

  it('D7 补口：AI 态发送订单卡本地即时渲染成卡（不等刷新）', async () => {
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (b) => b.textContent?.includes('发订单给客服'),
    )
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    const sheet = c.querySelector('[aria-label="订单选择"]')
    expect(sheet).not.toBeNull()
    const sendBtn = Array.from(sheet?.querySelectorAll('button') ?? []).find(
      (b) => b.textContent === '发送',
    )
    await act(async () => {
      sendBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    // 本地即时渲染：订单卡结构（徽标），无需等刷新/轮询
    expect(c.textContent).toContain('订单 MO-AAA111')
    expect(c.textContent).toContain('订单卡')
  })

  it('订单抽屉搜索本地过滤 + 常见问题 Tab + 兜底关闭并聚焦咨询输入框', async () => {
    const c = await mount()
    const chip = Array.from(c.querySelectorAll('[aria-label="快捷入口"] button')).find(
      (b) => b.textContent?.includes('发订单给客服'),
    )
    await act(async () => {
      chip?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await act(async () => {})
    const sheet = c.querySelector('[aria-label="订单选择"]') as HTMLElement
    const input = sheet.querySelector('input[type="search"]') as HTMLInputElement
    expect(input).not.toBeNull()
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )?.set
    await act(async () => {
      setter?.call(input, '耳机')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(sheet.textContent).toContain('MO-AAA111')
    expect(sheet.textContent).not.toContain('MO-BBB222')
    const faqTab = Array.from(sheet.querySelectorAll('[role="tab"]')).find(
      (b) => b.textContent?.includes('常见问题'),
    )
    await act(async () => {
      faqTab?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(sheet.textContent).toContain('物流追踪')
    const closeBtn = Array.from(sheet.querySelectorAll('button')).find(
      (b) => b.textContent?.includes('没有我想问的问题'),
    )
    await act(async () => {
      closeBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(c.querySelector('[aria-label="订单选择"]')).toBeNull()
    expect(document.activeElement).toBe(c.querySelector('[aria-label="咨询输入框"]'))
  })

  it('商城深链 cs2-order-ref：自动开抽屉并预选订单但不自动发送', async () => {
    window.sessionStorage.setItem('cs2-order-ref', 'MO-BBB222')
    const c = await mount()
    await act(async () => {})
    const sheet = c.querySelector('[aria-label="订单选择"]')
    expect(sheet).not.toBeNull()
    const row = sheet?.querySelector('[data-order-no="MO-BBB222"]')
    expect(row?.getAttribute('aria-selected')).toBe('true')
    // 预选只高亮：不自动发送
    expect(chatApi.streamChat).not.toHaveBeenCalled()
  })

  it('个人中心按会话终态显示已结束', async () => {
    cs2Api.fetchMyConversations.mockResolvedValueOnce([
      { conversation_id: 'active', summary: '进行中的咨询', conversation_status: 'open',
        handling_mode: 'ai', created_at: '2026-11-22T10:00:00Z' },
      { conversation_id: 'resolved', summary: '已处理的咨询', conversation_status: 'resolved',
        handling_mode: 'ai', created_at: '2026-11-22T10:00:00Z' },
      { conversation_id: 'handoff', summary: '坐席已关单', conversation_status: 'open',
        latest_handoff_closed: true, handling_mode: 'ai', created_at: '2026-11-22T10:00:00Z' },
    ])
    const c = await mount()
    await act(async () => c.querySelector<HTMLButtonElement>('[aria-label="打开个人中心"]')?.click())
    const drawer = c.querySelector('[aria-label="个人中心"]')
    expect(drawer?.textContent).toContain('进行中的咨询（进行中）')
    expect(drawer?.textContent).toContain('已处理的咨询（已结束）')
    expect(drawer?.textContent).toContain('坐席已关单（已结束）')
  })

  it('我的工单显示归属总数，下一页仍使用同一口径', async () => {
    cs2Api.listMyTickets
      .mockResolvedValueOnce({ items: [{ ticket_id: 'first', title: '首张工单' }], total: 14 })
      .mockResolvedValueOnce({ items: [{ ticket_id: 'last', title: '末张工单' }], total: 14 })
    const c = await mount()
    await openConversation(c, '退款咨询')
    const button = (label: string) => Array.from(c.querySelectorAll('button'))
      .find((item) => item.textContent?.includes(label))
    expect(button('我的工单（14）')).toBeDefined()
    await act(async () => button('我的工单（14）')?.click())
    expect(c.textContent).toContain('首张工单')
    await act(async () => button('下一页')?.click())
    expect(cs2Api.listMyTickets).toHaveBeenLastCalledWith(10, 10)
    expect(c.textContent).toContain('末张工单')
    expect(c.textContent).not.toContain('首张工单')
  })

  it('D7：card+order_ref 历史消息渲染成订单卡而非纯文本', async () => {
    cs2Api.fetchMyConversations.mockResolvedValueOnce([
      {
        conversation_id: 'conv-card',
        summary: '订单咨询',
        conversation_status: 'active',
        handling_mode: 'ai',
        created_at: '2026-11-22T10:00:00Z',
        last_activity_at: '2026-11-22T10:05:00Z',
        messages: [
          {
            message_id: '9',
            sender_type: 'user',
            content: '【订单咨询】订单号 MO-CARD1｜珍珠项链｜¥199.00｜待付款',
            content_type: 'card',
            attachments: [{ type: 'order_ref', order_no: 'MO-CARD1' }],
            created_at: '2026-11-22T10:00:00Z',
          },
          { message_id: '10', sender_type: 'assistant', content: '已收到您的订单咨询', content_type: 'text', created_at: '2026-11-22T10:00:05Z' },
        ],
      },
    ])
    const c = await mount()
    await openConversation(c, '订单咨询')
    // 订单卡结构：订单号 + 徽标（而非纯文本气泡）
    expect(c.textContent).toContain('订单 MO-CARD1')
    expect(c.textContent).toContain('订单卡')
    expect(c.textContent).toContain('¥199.00')
    // 普通文本消息不受影响
    expect(c.textContent).toContain('已收到您的订单咨询')
  })

  it('D3：选择会话后从服务端恢复 handoff 状态（人工接入横幅不再丢失）', async () => {
    // 持续返回 human_active：恢复调用与随后的轮询 effect 都要拿到一致状态，
    // 否则轮询的默认 mock（none）会把恢复出来的状态立刻覆盖掉
    cs2Api.fetchMyMessages.mockResolvedValue({
      conversation_id: 'conv-a',
      handoff_state: 'human_active',
      last_id: 2,
      messages: [],
      agent_typing: false,
      handoff_meta: { handoff_id: 'h-1', handoff_state: 'human_active', total_deadline_at: null, queue_position: 0 },
    })
    const c = await mount()
    await openConversation(c, '退款咨询')
    expect(cs2Api.fetchMyMessages).toHaveBeenCalledWith('conv-a', 0, 1)
    expect(c.textContent).toContain('人工客服已接入')
  })

  it('D11：已关单会话恢复时显示结束横幅，满意度卡即时出现（不等下一条消息）', async () => {
    // 回放恢复场景（U43）：服务端最新 handoff 为 closed，最后一条消息是
    // 关单系统消息——旧 showCsat 要求 lastMsg 为 assistant，卡要等用户再
    // 发一条 AI 轮次后才出现（触发时机错误）
    cs2Api.fetchMyMessages.mockResolvedValue({
      conversation_id: 'conv-a',
      handoff_state: 'closed',
      last_id: 4,
      agent_typing: false,
      handoff_meta: null,
      messages: [
        { message_id: '3', sender_type: 'human_agent', content: '您好，人工已接入', content_type: 'text', attachments: null, created_at: '2026-11-23T10:00:00Z' },
        { message_id: '4', sender_type: 'system', content: '已为您登记留言工单 GD-1，24 小时内回访', content_type: 'text', attachments: null, created_at: '2026-11-23T10:05:00Z' },
      ],
    })
    // selectSession 的 draft 来自会话列表快照：showCsat 要求历史里有人工
    // 消息（some role==='agent'），列表 mock 必须与恢复态一致
    cs2Api.fetchMyConversations.mockResolvedValue([
      {
        conversation_id: 'conv-a',
        summary: '退款咨询',
        conversation_status: 'active',
        handling_mode: 'human',
        created_at: '2026-11-23T10:00:00Z',
        last_activity_at: '2026-11-23T10:05:00Z',
        messages: [
          { message_id: '1', sender_type: 'user', content: '我要退款', content_type: 'text', attachments: null, created_at: '2026-11-23T10:00:00Z' },
          { message_id: '2', sender_type: 'assistant', content: '好的，请说明订单号', content_type: 'text', attachments: null, created_at: '2026-11-23T10:00:05Z' },
          { message_id: '3', sender_type: 'human_agent', content: '您好，人工已接入', content_type: 'text', attachments: null, created_at: '2026-11-23T10:01:00Z' },
          { message_id: '4', sender_type: 'system', content: '已为您登记留言工单 GD-1，24 小时内回访', content_type: 'text', attachments: null, created_at: '2026-11-23T10:05:00Z' },
        ],
      },
    ])
    const c = await mount()
    await openConversation(c, '退款咨询')
    await act(async () => {})
    // 灰横幅（closed 分支）与满意度卡即时渲染
    expect(c.textContent).toContain('人工服务已结束')
    expect(c.querySelector('[aria-label="满意度星级"]')).not.toBeNull()
  })

  it('D11：关单轮次补拉最后一轮增量（关单系统消息不丢），终态停轮询', async () => {
    vi.useFakeTimers()
    try {
      const closedEmpty = {
        conversation_id: 'conv-a',
        handoff_state: 'closed',
        last_id: 4,
        messages: [],
        agent_typing: false,
        handoff_meta: null,
      }
      const responses: Array<Record<string, unknown>> = [
        // selectSession 恢复（0,1）
        { ...closedEmpty, handoff_state: 'human_active', last_id: 2 },
        // poll#1（0,50）：人工消息增量
        {
          conversation_id: 'conv-a',
          handoff_state: 'human_active',
          last_id: 3,
          agent_typing: false,
          handoff_meta: null,
          messages: [
            { message_id: '3', sender_type: 'human_agent', content: '您好，人工已接入', content_type: 'text', attachments: null, created_at: '2026-11-23T10:00:00Z' },
          ],
        },
        // poll#2（0,50）：关单——system 消息在 poll#1 窗口后才落库
        {
          conversation_id: 'conv-a',
          handoff_state: 'closed',
          last_id: 4,
          agent_typing: false,
          handoff_meta: null,
          messages: [
            { message_id: '4', sender_type: 'system', content: '已为您登记留言工单 GD-1，24 小时内回访', content_type: 'text', attachments: null, created_at: '2026-11-23T10:05:00Z' },
          ],
        },
        // 补拉（last_id=4,50）
        { ...closedEmpty },
      ]
      cs2Api.fetchMyMessages.mockImplementation(async () => {
        const next = responses.shift()
        return (next ?? closedEmpty) as Awaited<ReturnType<typeof cs2Api.fetchMyMessages>>
      })
      const c = await mount()
      await openConversation(c, '退款咨询')
      // handoffState 保持 human_active（值未变），等 3s 轮询 interval 触发 poll#2
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      // 关单系统消息经补拉渲染（D10 关联），灰横幅 + 满意度卡出现
      expect(c.textContent).toContain('已为您登记留言工单 GD-1')
      expect(c.textContent).toContain('人工服务已结束')
      expect(c.querySelector('[aria-label="满意度星级"]')).not.toBeNull()
      expect(cs2Api.fetchMyMessages).toHaveBeenCalledWith('conv-a', 4, 50)
    } finally {
      vi.useRealTimers()
    }
  })
})
