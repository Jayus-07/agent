import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import TravelHome from '@/components/travel/v2/TravelHome'

vi.mock('@/api/travelV2', () => ({
  fetchTravelTemplatesV2: vi.fn().mockResolvedValue({ templates: [] }),
  fetchTravelTripsV2: vi.fn().mockResolvedValue({ trips: [] }),
  archiveTravelTripV2: vi.fn(),
}))

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container?.remove()
  root = null
  container = null
})

it('旅游首页提供自然语言规划、精选模板、V2近期行程和三种实时查询入口', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)

  await act(async () => root?.render(<TravelHome />))

  expect(container.querySelector('textarea[aria-label="描述你的旅行"]')).not.toBeNull()
  expect(container.textContent).toContain('精选旅行模板')
  expect(container.textContent).toContain('暂时没有精选模板')
  expect(container.textContent).not.toContain('正在加载平台精选模板')
  expect(container.textContent).toContain('我的最近行程')
  expect(container.textContent).toContain('查酒店')
  expect(container.textContent).toContain('查美食')
  expect(container.textContent).toContain('查动车')
  expect(container.querySelector('form[action="/travel/chat"]')).not.toBeNull()

  const hotelShortcut = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('查酒店'))
  expect(hotelShortcut).toBeDefined()
  await act(async () => hotelShortcut?.click())
  expect(container.textContent).toContain('查询酒店')
})
