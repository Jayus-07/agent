import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createTrip: vi.fn(),
  fetchTrip: vi.fn(),
  fetchTripConversation: vi.fn(),
  saveTrip: vi.fn(),
  setMessages: vi.fn(),
  stream: vi.fn(),
  messages: [] as Array<Record<string, unknown>>,
  conversationRequests: [] as Array<{ conversationId: string; enabled: boolean }>,
}))

vi.mock('@/api/travel', () => ({
  streamTravelPlanV2: mocks.stream,
}))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  createTravelTripV2: mocks.createTrip,
  fetchTravelTripV2: mocks.fetchTrip,
  fetchTravelTripConversationV2: mocks.fetchTripConversation,
  saveTravelTripDocumentV2: mocks.saveTrip,
}))
vi.mock('@/components/travel/useTravelConversation', () => ({
  useTravelConversation: (conversationId: string, options?: { enabled?: boolean }) => {
    const enabled = options?.enabled !== false
    mocks.conversationRequests.push({ conversationId, enabled })
    return { messages: enabled ? mocks.messages : [], setMessages: mocks.setMessages, syncError: '' }
  },
}))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => ({ tenantId: 'tenant-a', userId: 'user-a' }) }))

import TravelChatPage from './TravelChatPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('V2 旅游规划对话', () => {
  beforeEach(() => {
    mocks.stream.mockReset()
    mocks.fetchTrip.mockReset()
    mocks.fetchTripConversation.mockReset().mockImplementation(async (tripId: string) => ({
      trip_id: tripId, conversation_id: null, linked: false, server_now: '2026-10-17T12:00:00.000Z',
    }))
    mocks.setMessages.mockReset().mockImplementation((update) => {
      const next = typeof update === 'function' ? update(mocks.messages) : update
      mocks.messages.splice(0, mocks.messages.length, ...next)
    })
    mocks.messages.splice(0)
    mocks.conversationRequests.splice(0)
    window.history.replaceState({}, '', '/travel/chat')
    mocks.stream.mockImplementation(async function* () {
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
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('将真实规划 SSE 完成结果转换并创建 V2 正式行程', async () => {
    window.history.replaceState({}, '', '/travel/chat?campaign=autumn&prompt=keep-me')
    mocks.createTrip.mockImplementation(async (input: { title: string; document: { schema_version: number } }) => ({
      trip_id: 'v2-trip-123', title: input.title, status: 'active', revision: 1,
      document: input.document, source_template_id: null, source_template_version: null,
      created_at: '2026-10-09T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
    }))
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(TravelChatPage, { initialPrompt: '帮我安排杭州一日游' }))
      await Promise.resolve()
    })
    const initialUrl = new URL(window.location.href)
    expect(initialUrl.searchParams.get('conversation_id')).toMatch(/^travel-/)
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.createTrip).not.toHaveBeenCalled()
    expect(container.textContent).toContain('草稿已生成')
    expect(container.textContent).toContain('西湖')
    expect(container.textContent).toContain('行程草稿 · 待确认保存')
    const savedUrlBefore = new URL(window.location.href)
    expect(savedUrlBefore.searchParams.get('trip_id')).toBeNull()
    const saveButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '保存为正式行程')!
    await act(async () => {
      saveButton.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.createTrip).toHaveBeenCalledOnce()
    const savedUrl = new URL(window.location.href)
    expect(savedUrl.searchParams.get('trip_id')).toBe('v2-trip-123')
    expect(savedUrl.searchParams.get('conversation_id')).toBe(initialUrl.searchParams.get('conversation_id'))
    expect(savedUrl.searchParams.get('campaign')).toBe('autumn')
    expect(savedUrl.searchParams.get('prompt')).toBe('keep-me')
    expect(mocks.createTrip.mock.calls[0][0].document.schema_version).toBe(2)
    const savedMessage = mocks.setMessages.mock.calls
      .map(([update]) => typeof update === 'function' ? update([]) : update)
      .flat()
      .find((message: { text?: string }) => message.text?.includes('行程已保存到 V2'))
    expect(savedMessage).toBeTruthy()
    expect(container.querySelector('a[href^="/travel/itineraries/v2-trip-123?conversation_id="]')).not.toBeNull()
    expect(mocks.saveTrip).not.toHaveBeenCalled()
    await act(async () => root.unmount())
  })

  it('草稿创建回执丢失时保持 UNKNOWN，安全重试复用原键和完全相同 payload', async () => {
    mocks.createTrip
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockImplementationOnce(async (input: { title: string; document: { schema_version: number; days: unknown[] } }) => ({
        trip_id: 'v2-trip-replayed', title: input.title, status: 'active', revision: 1,
        document: input.document, source_template_id: null, source_template_version: null,
        created_at: '2026-10-09T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
      } as never))
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(TravelChatPage, { initialPrompt: '帮我安排杭州一日游' }))
      await Promise.resolve()
    })
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.createTrip).not.toHaveBeenCalled()
    const saveButton = [...container.querySelectorAll('button')].find((button) => button.textContent === '保存为正式行程')!
    await act(async () => {
      saveButton.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('状态待核实')
    expect(container.textContent).toContain('服务端可能已经完成操作')
    expect(mocks.createTrip).toHaveBeenCalledOnce()
    const originalRequest = mocks.createTrip.mock.calls[0]
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === '使用同一请求安全重试')!
    await act(async () => {
      retry.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.createTrip).toHaveBeenCalledTimes(2)
    expect(mocks.createTrip.mock.calls[1]).toEqual(originalRequest)
    expect(new URL(window.location.href).searchParams.get('trip_id')).toBe('v2-trip-replayed')
    expect(container.querySelector('a[href^="/travel/itineraries/v2-trip-replayed?conversation_id="]')).not.toBeNull()
    expect(container.textContent).toContain('行程已保存到 V2')
    await act(async () => root.unmount())
  })

  it('澄清结果渲染结构化选项，点击选项以该内容继续对话', async () => {
    // 第一次调用返回澄清；第二次回落到 beforeEach 的 plan generator（点击选项后进草稿预览）
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
    })
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(TravelChatPage, { initialPrompt: '帮我安排行程' }))
      await Promise.resolve()
    })
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('需要补充信息')
    const optionTwo = [...container.querySelectorAll('button')].find((button) => button.textContent === '两天')!
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
    expect(container.textContent).toContain('草稿已生成')
    await act(async () => root.unmount())
  })

  it('清除本地缓存后仍通过 URL Trip ID 恢复正式行程', async () => {
    const tripId = '00000000-0000-4000-8000-000000000071'
    const savedTrip = {
      trip_id: tripId, title: '杭州正式行程', status: 'active', revision: 3,
      source_template_id: null, source_template_version: null,
      created_at: '2026-10-09T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
      document: {
        schema_version: 2,
        brief: { origin: '上海', destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-12-01', day_count: 1, travelers: { adults: 1, children: 0 }, budget: { amount: 3000, currency: 'CNY' }, pace: 'relaxed', interests: [], requirements: [] },
        selections: [], days: [], arrangements: { lodgings: [], intercity_trains: [] },
        totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
        health: { status: 'ok', issues: [] },
      },
    }
    window.localStorage.clear()
    window.history.replaceState({}, '', `/travel/chat?trip_id=${tripId}&campaign=shared-link`)
    mocks.fetchTrip.mockResolvedValue(savedTrip)
    mocks.fetchTripConversation.mockResolvedValue({
      trip_id: tripId, conversation_id: `travel-v2-trip-${tripId}`, linked: true,
    })
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(createElement(TravelChatPage, { initialTripId: tripId }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.fetchTrip).toHaveBeenCalledWith(tripId)
    expect(mocks.createTrip).not.toHaveBeenCalled()
    expect(container.textContent).toContain('正式行程对话 · Revision v3')
    expect(new URL(window.location.href).searchParams.get('trip_id')).toBe(tripId)
    expect(new URL(window.location.href).searchParams.get('conversation_id')).toBe(`travel-v2-trip-${tripId}`)
    expect(new URL(window.location.href).searchParams.get('campaign')).toBe('shared-link')
    await act(async () => root.unmount())
  })

  it('Trip URL 中的会话候选只能在服务端确认原始关联后被替换并加载', async () => {
    const tripId = '00000000-0000-4000-8000-000000000073'
    const candidate = 'travel-url-candidate-from-another-device'
    const originalConversation = 'travel-original-server-linked'
    const savedTrip = {
      trip_id: tripId, title: '杭州正式行程', status: 'active', revision: 3,
      source_template_id: null, source_template_version: null,
      created_at: '2026-10-09T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
      document: {
        schema_version: 2,
        brief: { origin: '上海', destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-12-01', day_count: 1, travelers: { adults: 1, children: 0 }, budget: { amount: 3000, currency: 'CNY' }, pace: 'relaxed', interests: [], requirements: [] },
        selections: [], days: [], arrangements: { lodgings: [], intercity_trains: [] },
        totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
        health: { status: 'ok', issues: [] },
      },
    }
    window.history.replaceState({}, '', `/travel/chat?trip_id=${tripId}&conversation_id=${candidate}`)
    mocks.fetchTrip.mockResolvedValue(savedTrip)
    mocks.fetchTripConversation.mockResolvedValue({
      trip_id: tripId, conversation_id: originalConversation, linked: true,
    })
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(createElement(TravelChatPage, {
        initialTripId: tripId, initialConversationId: candidate,
      }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.fetchTripConversation).toHaveBeenCalledWith(tripId)
    expect(mocks.conversationRequests.some((entry) =>
      entry.conversationId === candidate && entry.enabled)).toBe(false)
    expect(mocks.conversationRequests).toContainEqual({
      conversationId: originalConversation, enabled: true,
    })
    expect(new URL(window.location.href).searchParams.get('conversation_id')).toBe(originalConversation)
    expect(container.textContent).toContain('服务端关联不一致')
    await act(async () => root.unmount())
  })

  it('大写 UUID URL 使用服务端规范 Trip ID 恢复原会话', async () => {
    const tripId = '00000000-0000-4000-8000-000000000073'
    const routeTripId = tripId.toUpperCase()
    const originalConversation = 'travel-original-case-normalized'
    const savedTrip = {
      trip_id: tripId, title: '大小写恢复行程', status: 'active', revision: 3,
      source_template_id: null, source_template_version: null,
      created_at: '2026-10-09T00:00:00Z', updated_at: '2026-10-09T00:00:00Z',
      document: {
        schema_version: 2,
        brief: { origin: '上海', destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-12-01', day_count: 1, travelers: { adults: 1, children: 0 }, budget: { amount: 3000, currency: 'CNY' }, pace: 'relaxed', interests: [], requirements: [] },
        selections: [], days: [], arrangements: { lodgings: [], intercity_trains: [] },
        totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
        health: { status: 'ok', issues: [] },
      },
    }
    window.history.replaceState({}, '', `/travel/chat?trip_id=${routeTripId}`)
    mocks.fetchTrip.mockResolvedValue(savedTrip)
    mocks.fetchTripConversation.mockResolvedValue({
      trip_id: tripId, conversation_id: originalConversation, linked: true,
      server_now: '2026-10-17T12:00:00.000Z',
    })
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(createElement(TravelChatPage, { initialTripId: routeTripId }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('正式行程对话 · Revision v3')
    expect(mocks.conversationRequests).toContainEqual({ conversationId: originalConversation, enabled: true })
    expect(mocks.fetchTripConversation).toHaveBeenCalledWith(routeTripId)
    expect(mocks.createTrip).not.toHaveBeenCalled()
    await act(async () => root.unmount())
  })

  it('没有服务端会话关联时不读取 URL 候选或任何对话历史', async () => {
    const tripId = '00000000-0000-4000-8000-000000000074'
    const candidate = 'travel-unlinked-url-candidate'
    window.history.replaceState({}, '', `/travel/chat?trip_id=${tripId}&conversation_id=${candidate}`)
    mocks.fetchTrip.mockResolvedValue({
      trip_id: tripId, title: '旧行程', status: 'active', revision: 1,
      document: {
        schema_version: 2,
        brief: { destination: '杭州', day_count: 1, travelers: { adults: 1, children: 0 }, budget: { amount: null, currency: 'CNY' }, timezone: 'Asia/Shanghai' },
        selections: [], days: [], arrangements: { lodgings: [], intercity_trains: [] },
        totals: { currency: 'CNY', estimated_cost: null }, health: { status: 'ok', issues: [] },
      },
    })
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(createElement(TravelChatPage, {
        initialTripId: tripId, initialConversationId: candidate,
      }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.fetchTripConversation).toHaveBeenCalledWith(tripId)
    expect(mocks.conversationRequests.every((entry) => !entry.enabled)).toBe(true)
    expect(container.textContent).toContain('没有可验证的服务端原始会话关联')
    expect(new URL(window.location.href).searchParams.get('conversation_id')).toBe(candidate)
    expect(mocks.createTrip).not.toHaveBeenCalled()
    await act(async () => root.unmount())
  })

  it('URL 行程读取失败时只允许重试读取，不创建第二个 Trip', async () => {
    const tripId = '00000000-0000-4000-8000-000000000072'
    window.history.replaceState({}, '', `/travel/chat?trip_id=${tripId}`)
    mocks.fetchTrip.mockRejectedValue(new Error('network unavailable'))
    const container = document.createElement('div')
    const root = createRoot(container)

    await act(async () => {
      root.render(createElement(TravelChatPage, { initialTripId: tripId }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('不会创建或覆盖行程')
    expect(mocks.createTrip).not.toHaveBeenCalled()
    await act(async () => {
      container.querySelector('button')?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.createTrip).not.toHaveBeenCalled()
    expect(new URL(window.location.href).searchParams.get('trip_id')).toBe(tripId)
    await act(async () => root.unmount())
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
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => {
      root.render(createElement(TravelChatPage, { initialPrompt: '规划福州一日游' }))
      await Promise.resolve()
    })
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
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
    expect(container.textContent).not.toContain('travel-decision-token')
    await act(async () => root.unmount())

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
    const resumed = document.createElement('div')
    const resumedRoot = createRoot(resumed)
    await act(async () => {
      resumedRoot.render(createElement(TravelChatPage, { initialPrompt: '保留' }))
      await Promise.resolve()
    })
    await act(async () => {
      resumed.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.stream.mock.calls.at(-1)?.[2].actionPayload).toEqual({ decision_token: token })
    expect(resumed.textContent).not.toContain('travel-decision-token')
    await act(async () => resumedRoot.unmount())
  })
})
