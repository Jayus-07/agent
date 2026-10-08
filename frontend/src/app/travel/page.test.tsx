import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import TravelPage from './page'

const { stream } = vi.hoisted(() => ({ stream: vi.fn() }))
vi.mock('@/api/travel', () => ({
  streamTravelPlan: stream, fetchTravelRecommendations: vi.fn().mockResolvedValue([]),
  fetchTravelPlanList: vi.fn().mockResolvedValue([]),
  fetchTravelPlanLatest: vi.fn().mockResolvedValue({}), fetchItineraryIcs: vi.fn(),
  reverseGeocodeTravelOrigin: vi.fn(), sendTravelFeedback: vi.fn(),
  // 偏好引导（2026-10-08）：测试统一视为老用户（不弹问卷）
  fetchMyPreferences: vi.fn().mockResolvedValue({ origin: '福州', preferences: [], pace: 'relaxed', diet: '', lodging: '', transport: '' }),
  isEmptyPrefs: () => false,
}))
vi.mock('@/hooks/useBudgetStatus', () => ({ useBudgetStatus: () => ({ blocked: false }) }))
vi.mock('@/lib/auth', () => ({ getCachedUser: () => null }))
// 2026-10-07 顶栏头像进设置页需要 router —— 测试无 App Router 上下文，mock 之
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/travel/ItineraryView', () => ({ default: () => null }))
vi.mock('@/components/travel/TravelPlanList', () => ({ default: () => null }))
vi.mock('@/components/agent/TaskSidebar', () => ({ default: () => null }))
vi.mock('@/components/agent/SidebarRail', () => ({ default: () => null }))
vi.mock('@/components/travel/TravelChatDrawer', () => ({ default: (props: { onStartNewTrip: () => void }) => <button onClick={props.onStartNewTrip}>测试新行程</button> }))

let root: Root
let container: HTMLDivElement
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  sessionStorage.clear()
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
