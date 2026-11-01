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
})
