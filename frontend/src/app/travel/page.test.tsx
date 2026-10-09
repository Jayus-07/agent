import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import TravelPage from './page'

const { stream, fetchLatest } = vi.hoisted(() => ({ stream: vi.fn(), fetchLatest: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/travel', () => ({
  streamTravelPlan: stream, fetchTravelRecommendations: vi.fn().mockResolvedValue([]),
  fetchTravelPlanList: vi.fn().mockResolvedValue([]),
  fetchTravelPlanLatest: fetchLatest, fetchItineraryIcs: vi.fn(),
  reverseGeocodeTravelOrigin: vi.fn(), sendTravelFeedback: vi.fn(),
  // 偏好引导（2026-10-08）：测试统一视为老用户（不弹问卷）
  fetchMyPreferences: vi.fn().mockResolvedValue({ origin: '福州', preferences: [], pace: 'relaxed', diet: '', lodging: '', transport: '' }),
  isEmptyPrefs: () => false,
}))
vi.mock('@/hooks/useBudgetStatus', () => ({ useBudgetStatus: () => ({ blocked: false }) }))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => null }))
// 2026-10-07 顶栏头像进设置页需要 router —— 测试无 App Router 上下文，mock 之
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/travel/ItineraryView', () => ({
  default: ({ itinerary, draftPreview }: { itinerary: { plan_version: number }; draftPreview?: boolean }) => (
    <div data-testid="itinerary-preview" data-version={itinerary.plan_version} data-draft={String(Boolean(draftPreview))} />
  ),
}))
vi.mock('@/components/travel/CandidatesPanel', () => ({ default: () => null }))
vi.mock('@/components/travel/TravelPlanList', () => ({
  default: ({ onRestore }: { onRestore: (cid: string) => void }) => (
    <button onClick={() => onRestore('restored-conversation')}>恢复历史行程</button>
  ),
}))
vi.mock('@/components/agent/TaskSidebar', () => ({
  default: ({ renderHistory }: { renderHistory: (state: { keyword: string; refreshKey: number; onRefreshingChange: () => void }) => ReactNode }) => (
    <>{renderHistory({ keyword: '', refreshKey: 0, onRefreshingChange: () => undefined })}</>
  ),
}))
vi.mock('@/components/agent/SidebarRail', () => ({ default: () => null }))
vi.mock('@/components/travel/TravelChatDrawer', () => ({
  default: (props: { onStartNewTrip: () => void; handoverUserMessage: string }) => (
    <>
      <div data-testid="handover-message">{props.handoverUserMessage}</div>
      <button onClick={props.onStartNewTrip}>测试新行程</button>
    </>
  ),
}))

let root: Root
let container: HTMLDivElement
async function mount(initialPlanState?: unknown) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  sessionStorage.clear()
  if (initialPlanState) sessionStorage.setItem('travel:plan-state', JSON.stringify(initialPlanState))
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelPage />))
}
function example(title: string) {
  return [...container.querySelectorAll('button')].find((button) => button.textContent?.includes(title))!
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

it.each(['福州美食周末', '福州 → 厦门', '泉州慢游'])('示例 %s 请求包含点击当天计算的明天日期', async (title) => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 11, 31, 12))
  stream.mockImplementation(async function* () { yield { event: 'done', data: { result: { status: 'answered', final_answer: 'ok', itinerary: null } } } })
  await mount()
  await act(async () => example(title).click())
  expect(stream).toHaveBeenCalledTimes(1)
  expect(stream.mock.calls[0][0]).toContain('2027-01-01')
  expect(stream.mock.calls[0][2]).toMatchObject({
    mode: 'plan',
    briefInput: { start_date: '2027-01-01' },
  })
})

it('组合交通示例提交结构化行程 Brief 与查票原话', async () => {
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: { status: 'answered', final_answer: 'ok', itinerary: null } } }
  })
  await mount()
  await act(async () => example('福州 → 厦门').click())

  expect(stream.mock.calls[0][0]).toContain('查高铁票')
  expect(stream.mock.calls[0][2]).toMatchObject({
    mode: 'plan',
    briefInput: {
      origin: '福州', destination: '厦门', days: 2,
      party_size: 2, preferences: ['美食'], pace: 'moderate',
    },
  })
})

it('同一批次连点示例只执行一次', async () => {
  let release!: () => void
  stream.mockImplementation(async function* () {
    await new Promise<void>((resolve) => { release = resolve })
    yield { event: 'done', data: { result: { status: 'answered', final_answer: 'ok', itinerary: null } } }
  })
  await mount()
  const button = example('福州美食周末')
  await act(async () => { button.click(); button.click() })
  expect(stream).toHaveBeenCalledTimes(1)
  await act(async () => release())
})

it('开启新行程后忽略旧请求迟到的失败结果', async () => {
  let release!: () => void
  stream.mockImplementation(async function* () {
    await new Promise<void>((resolve) => { release = resolve })
    yield { event: 'done', data: { result: { status: 'failed', final_answer: '旧请求迟到失败', itinerary: null } } }
  })
  await mount()
  await act(async () => example('福州美食周末').click())
  await act(async () => example('测试新行程').click())
  await act(async () => release())
  expect(container.textContent).not.toContain('旧请求迟到失败')
  expect(container.textContent).toContain('福州美食周末')
})

it('恢复历史行程时清空上一条新会话移交语', async () => {
  const restoredPlan = {
    conversation_id: 'restored-conversation', plan_version: 1, plan_status: 'confirmed',
    destination: '福州', created_at: '2026-10-08T00:00:00Z',
    itinerary: {
      brief: {
        destination: '福州', origin: '', start_date: '2026-10-10', days: 1,
        party_size: 2, budget_cny: 0, preferences: [], must_go: [], avoid: [],
        pace: 'moderate', diet: '', lodging: '', transport: '',
      },
      days: [{ day_index: 1, day_date: '2026-10-10', items: [], active_minutes: 0, transit_minutes: 0, cost_cny: 0 }],
      cost: { tickets: 0, meals: 0, lodging: 0, transit: 0 },
      status: 'ready', plan_version: 1, warnings: [],
    },
  }
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: { status: 'answered', final_answer: '已完成泉州规划', itinerary: null } } }
  })
  await mount()
  await act(async () => example('泉州慢游').click())
  expect(container.querySelector('[data-testid="handover-message"]')?.textContent).toContain('泉州')
  fetchLatest.mockResolvedValue(restoredPlan)
  await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="展开任务栏"]')?.click())

  await act(async () => example('恢复历史行程').click())

  expect(container.querySelector('[data-testid="handover-message"]')?.textContent).toBe('')
})

it('桌面中栏默认显示 Draft 全貌，并可只读切回 Active', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('min-width: 1280px'),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
  const makeItinerary = (version: number) => ({
    brief: {
      destination: '杭州', origin: '上海', start_date: '2026-10-10', days: 1,
      party_size: 2, budget_cny: 3000, preferences: [], must_go: [], avoid: [],
      pace: 'moderate', diet: '', lodging: '', transport: '',
    },
    days: [{ day_index: 1, day_date: '2026-10-10', items: [], active_minutes: 0, transit_minutes: 0, cost_cny: 0 }],
    cost: { tickets: 0, meals: 0, lodging: 0, transit: 0 },
    status: 'ready', plan_version: version, warnings: [],
  })
  await mount({
    plan: { status: 'ready', final_answer: '', plan_status: 'confirmed', itinerary: makeItinerary(12) },
    pending: {
      status: 'ready', final_answer: '', plan_status: 'waiting_confirmation',
      base_plan_version: 12, itinerary: makeItinerary(13),
    },
    notice: '', discarded: [],
  })

  let preview = container.querySelector('[data-testid="itinerary-preview"]')!
  expect(preview.getAttribute('data-version')).toBe('13')
  expect(preview.getAttribute('data-draft')).toBe('true')
  expect(container.textContent).toContain('草案 v13 · 未应用')
  const activeButton = [...container.querySelectorAll('button')]
    .find((button) => button.textContent?.includes('正式版 v12'))!
  await act(async () => activeButton.click())
  preview = container.querySelector('[data-testid="itinerary-preview"]')!
  expect(preview.getAttribute('data-version')).toBe('12')
  expect(preview.getAttribute('data-draft')).toBe('false')
})
