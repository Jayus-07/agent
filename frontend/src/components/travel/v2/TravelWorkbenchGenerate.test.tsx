import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createTrip: vi.fn(),
  stream: vi.fn(),
  setMessages: vi.fn(),
  messages: [] as Array<Record<string, unknown>>,
  routerReplace: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mocks.routerReplace }),
}))
vi.mock('@/api/travel', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travel')>()),
  streamTravelPlanV2: mocks.stream,
}))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  createTravelTripV2: mocks.createTrip,
}))
vi.mock('@/components/travel/useTravelConversation', () => ({
  useTravelConversation: () => ({ messages: mocks.messages, setMessages: mocks.setMessages, syncError: '' }),
}))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => ({ tenantId: 'tenant-a', userId: 'user-a' }) }))

import TravelWorkbenchGenerate from './TravelWorkbenchGenerate'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function planStreamMock() {
  return async function* () {
    yield {
      event: 'done',
      data: {
        status: 'success',
        result: {
          status: 'success',
          plan_status: 'confirmed',
          final_answer: '杭州行程已安排。',
          itinerary: {
            brief: { destination: '杭州', origin: '', start_date: '2026-12-01', days: 1, party_size: 1, budget_cny: null, pace: 'relaxed', preferences: [], must_go: [], avoid: [], diet: '', lodging: '', transport: '' },
            days: [{ day_index: 1, day_date: '2026-12-01', active_minutes: 90, transit_minutes: 0, cost_cny: 0, items: [{ title: '西湖', kind: 'visit', start: '09:00', end: '10:30', minutes: 90, wait_minutes: 0, note: '', poi: { poi_id: 'poi-west-lake', name: '西湖', lat: 30.25, lng: 120.15, ticket_cny: 0, source: 'amap', location_status: 'verified', required: false } }], legs: [] }],
            cost: { tickets: 0, meals: 0, lodging: 0, transit: 0, total: 0 },
            warnings: [], intercity: [],
          },
        },
      },
    }
  }
}

function mounted() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root, unmount: () => { act(() => root.unmount()); container.remove() } }
}

describe('TravelWorkbenchGenerate（P4a 工作台生成态）', () => {
  beforeEach(() => {
    mocks.stream.mockReset()
    mocks.createTrip.mockReset()
    mocks.setMessages.mockReset().mockImplementation((update) => {
      const next = typeof update === 'function' ? update(mocks.messages) : update
      mocks.messages.splice(0, mocks.messages.length, ...next)
    })
    mocks.messages.splice(0)
    mocks.routerReplace.mockReset()
    window.history.replaceState({}, '', '/travel/itineraries/new')
  })

  afterEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    document.body.innerHTML = ''
  })

  it('空态渲染三列骨架（生成面板引导 + 草稿概览空态 + 对话伴随）', async () => {
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州两日慢游' }))
        await Promise.resolve()
      })
      expect(view.container.textContent).toContain('正在生成新行程')
      expect(view.container.textContent).toContain('还没有草稿')
      expect(view.container.textContent).toContain('AI 旅行助手')
      // initialPrompt 进入对话输入框（首页携 prompt 直达）
      const textarea = view.container.querySelector<HTMLTextAreaElement>('#workbench-generate-input')!
      expect(textarea.value).toBe('杭州两日慢游')
    } finally {
      view.unmount()
    }
  })

  it('SSE 完成后出逐日草稿卡与草稿地图，保存确认后 replace 进正式工作台', async () => {
    mocks.stream.mockImplementation(planStreamMock())
    mocks.createTrip.mockImplementation(async (input: { title: string; document: { schema_version: number } }) => ({
      trip_id: 'v2-trip-wb', title: input.title, status: 'active', revision: 1,
      document: input.document, source_template_id: null, source_template_version: null,
      created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
      conversation_id: 'conv-server-1',
    }))
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '杭州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(mocks.createTrip).not.toHaveBeenCalled() // A5 两段式：草稿先出，确认才落库
      expect(view.container.textContent).toContain('草稿已生成')
      expect(view.container.textContent).toContain('西湖')
      expect(view.container.textContent).toContain('第 1 天 · 第 1 天') // 逐日卡标题（adapter 日标题）

      const saveButton = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '保存为正式行程')!
      await act(async () => {
        saveButton.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(mocks.createTrip).toHaveBeenCalledOnce()
      expect(mocks.createTrip.mock.calls[0][0].document.schema_version).toBe(2)
      // 保存成功 → replace 进正式工作台（服务端会话关联为权威）
      expect(mocks.routerReplace).toHaveBeenCalledWith('/travel/itineraries/v2-trip-wb?conversation_id=conv-server-1')
    } finally {
      view.unmount()
    }
  })

  // —— P4b 测试迁移（自 TravelChatPage.test）：生成引擎语义的回归网随
  // chat 页退役迁入本文件，断言语义保持不变（保存成功后入口断言按
  // WorkbenchGenerate 语义改为 routerReplace）。 ——

  it('草稿创建回执丢失时保持 UNKNOWN，安全重试复用原键和完全相同 payload', async () => {
    mocks.createTrip
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementationOnce(async (input: { title: string; document: { schema_version: number; days: unknown[] } }) => ({
        trip_id: 'v2-trip-replayed', title: input.title, status: 'active', revision: 1,
        document: input.document, source_template_id: null, source_template_version: null,
        created_at: '2026-11-02T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
      } as never))
    mocks.stream.mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '帮我安排杭州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(mocks.createTrip).not.toHaveBeenCalled()
      const saveButton = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '保存为正式行程')!
      await act(async () => {
        saveButton.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(view.container.textContent).toContain('状态待核实')
      expect(view.container.textContent).toContain('服务端可能已经完成操作')
      expect(mocks.createTrip).toHaveBeenCalledOnce()
      const originalRequest = mocks.createTrip.mock.calls[0]
      const retry = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '使用同一请求安全重试')!
      await act(async () => {
        retry.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      // 红线：UNKNOWN 安全重试必须复用同一幂等键与完全相同 payload，不得另起请求
      expect(mocks.createTrip).toHaveBeenCalledTimes(2)
      expect(mocks.createTrip.mock.calls[1]).toEqual(originalRequest)
      const replacedHref = mocks.routerReplace.mock.calls.at(-1)?.[0] as string
      expect(replacedHref.startsWith('/travel/itineraries/v2-trip-replayed?conversation_id=')).toBe(true)
      expect(view.container.textContent).toContain('行程已保存到 V2')
    } finally {
      view.unmount()
    }
  })

  it('澄清结果渲染结构化选项，点击选项以该内容继续对话', async () => {
    // 第一次调用返回澄清；第二次回落到 plan generator（点击选项后进草稿预览）
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: {
          status: 'needs_clarification',
          result: {
            status: 'needs_clarification',
            result_kind: 'clarification',
            final_answer: '想去几天？',
            clarification: '想去几天？',
            clarification_options: [
              { label: '两天', days: 2, message: '两天行程' },
              { label: '三天', days: 3, message: '三天行程' },
            ],
          },
        },
      }
    }).mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '帮我安排行程' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(view.container.textContent).toContain('需要补充信息')
      const optionTwo = [...view.container.querySelectorAll('button')].find((button) => button.textContent === '两天')!
      expect(optionTwo).toBeTruthy()
      await act(async () => {
        optionTwo.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      // 选项点击即以该内容发送；第二次 stream 调用走默认 plan 分支进入草稿预览
      expect(mocks.setMessages.mock.calls.some(([update]) => {
        const next = typeof update === 'function' ? update(mocks.messages) : update
        return next.some((message: { role?: string; text?: string }) => message.role === 'user' && message.text === '两天')
      })).toBe(true)
      expect(view.container.textContent).toContain('草稿已生成')
    } finally {
      view.unmount()
    }
  })

  it('渲染 tool.card 查询结果卡，动作直通聊天管线', async () => {
    // 第一轮：tool.card 事件 + 纯问答结果（无 itinerary → answer）
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'tool.card',
        data: {
          card: {
            kind: 'food', title: '', provider: 'amap', disclosure: '价格为查询时点快照',
            items: [{ id: 'm-1', name: '厦门沙茶面', category: '闽菜', address: '中山路 1 号', rating: 4.5 }],
          },
        },
      }
      yield {
        event: 'done',
        data: { status: 'success', result: { status: 'success', final_answer: '找到几家本地餐馆。' } },
      }
    }).mockImplementation(planStreamMock())
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '今晚吃什么' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(view.container.textContent).toContain('餐饮推荐')
      expect(view.container.textContent).toContain('厦门沙茶面')
      expect(view.container.textContent).toContain('价格为查询时点快照')

      // 卡片动作直通既有聊天管线：把条目作为新消息发送（第二轮 stream）
      const askButton = [...view.container.querySelectorAll('button')].find((button) => button.textContent?.includes('帮我排进行程'))!
      expect(askButton).toBeTruthy()
      await act(async () => {
        askButton.click()
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
      expect(mocks.setMessages.mock.calls.some(([update]) => {
        const next = typeof update === 'function' ? update(mocks.messages) : update
        return next.some((message: { role?: string; text?: string }) => message.role === 'user' && message.text?.includes('厦门沙茶面'))
      })).toBe(true)
      expect(mocks.stream).toHaveBeenCalledTimes(2)
    } finally {
      view.unmount()
    }
  })

  it('持久化待决令牌但不显示给用户，刷新后随确认自动提交', async () => {
    const token = 'a'.repeat(24)
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: {
          status: 'needs_clarification',
          result: {
            status: 'needs_clarification',
            result_kind: 'clarification',
            final_answer: '请回复保留或移除指定地点。',
            itinerary: null,
            travel_context: { pending_decision: { decision_token: token } },
          },
        },
      }
    })
    const view = mounted()
    try {
      await act(async () => {
        view.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '规划福州一日游' }))
        await Promise.resolve()
      })
      await act(async () => {
        view.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      const updates = mocks.setMessages.mock.calls.map(([update]) => update)
      const storedMessages = updates.reduce<Array<{ role: string; text: string }>>(
        (current, update) => typeof update === 'function' ? update(current) : update,
        [],
      )
      const pendingMessage = storedMessages.find((message: { role: string; text: string }) =>
        message.role === 'assistant')
      expect(pendingMessage?.text).toContain(`[[travel-decision-token:${token}]]`)
      expect(view.container.textContent).not.toContain('travel-decision-token')
    } finally {
      view.unmount()
    }

    // 模拟刷新恢复：会话消息（含令牌）由服务端历史恢复后重新进入生成态
    mocks.messages.splice(0, mocks.messages.length, {
      id: 'server:pending', conversationId: 'travel-conversation', turnId: 'turn-1',
      role: 'assistant', text: `请回复保留。\n[[travel-decision-token:${token}]]`,
    })
    mocks.stream.mockImplementationOnce(async function* () {
      yield {
        event: 'done',
        data: { status: 'answered', result: { status: 'answered', final_answer: '已处理。', itinerary: null } },
      }
    })
    const resumed = mounted()
    try {
      await act(async () => {
        resumed.root.render(createElement(TravelWorkbenchGenerate, { initialPrompt: '保留' }))
        await Promise.resolve()
      })
      await act(async () => {
        resumed.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      expect(mocks.stream.mock.calls.at(-1)?.[2].actionPayload).toEqual({ decision_token: token })
      expect(resumed.container.textContent).not.toContain('travel-decision-token')
    } finally {
      resumed.unmount()
    }
  })
})
