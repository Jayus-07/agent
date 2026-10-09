import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createTrip: vi.fn(),
  saveTrip: vi.fn(),
  setMessages: vi.fn(),
}))

vi.mock('@/api/travel', () => ({
  streamTravelPlanV2: vi.fn(async function* () {
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
  }),
}))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  createTravelTripV2: mocks.createTrip,
  saveTravelTripDocumentV2: mocks.saveTrip,
}))
vi.mock('@/components/travel/useTravelConversation', () => ({
  useTravelConversation: () => ({ messages: [], setMessages: mocks.setMessages, syncError: '' }),
}))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => ({ tenantId: 'tenant-a', userId: 'user-a' }) }))

import TravelChatPage from './TravelChatPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

describe('V2 旅游规划对话', () => {
  afterEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('将真实规划 SSE 完成结果转换并创建 V2 正式行程', async () => {
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
    await act(async () => {
      container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.createTrip).toHaveBeenCalledOnce()
    expect(mocks.createTrip.mock.calls[0][0].document.schema_version).toBe(2)
    const savedMessage = mocks.setMessages.mock.calls
      .map(([update]) => typeof update === 'function' ? update([]) : update)
      .flat()
      .find((message: { text?: string }) => message.text?.includes('行程已自动保存'))
    expect(savedMessage).toBeTruthy()
    expect(container.querySelector('a[href="/travel/itineraries/v2-trip-123"]')).not.toBeNull()
    expect(mocks.saveTrip).not.toHaveBeenCalled()
    await act(async () => root.unmount())
  })
})
