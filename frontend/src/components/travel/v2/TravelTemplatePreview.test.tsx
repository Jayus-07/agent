import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TravelTemplateV2, TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ fetchTemplate: vi.fn(), copyTemplate: vi.fn(), push: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  fetchTravelTemplateV2: mocks.fetchTemplate,
  copyTravelTemplateV2: mocks.copyTemplate,
}))

import TravelTemplatePreview from './TravelTemplatePreview'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function template(): TravelTemplateV2 {
  return {
    slug: 'hangzhou-slow', title: '杭州湖山慢游', destination: '杭州', summary: '慢游路线',
    cover_image_url: null, tags: ['轻松'],
    content: { brief: { destination: '杭州', day_count: 1, travelers: { adults: 2, children: 0 }, budget: { amount: null } }, days: [{ day_id: 'day-1', date: null, items: [], legs: [] }] } as unknown as TravelTemplateV2['content'],
  } as TravelTemplateV2
}

let root: Root | null = null
let container: HTMLDivElement | null = null

async function renderPreview() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root?.render(createElement(TravelTemplatePreview, { slug: 'hangzhou-slow' })) })
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
}

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
})

beforeEach(() => {
  mocks.fetchTemplate.mockReset().mockResolvedValue(template())
  mocks.copyTemplate.mockReset()
  mocks.push.mockReset()
})

describe('旅游 V2 模板预览与复制', () => {
  it('只在复制 REST 成功后进入正式 Trip 工作台', async () => {
    let resolve!: (trip: TravelTripV2) => void
    mocks.copyTemplate.mockReturnValue(new Promise((accept) => { resolve = accept }))
    await renderPreview()

    const button = [...(container?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('复制为我的行程'))
    await act(async () => { button?.click() })
    expect(container?.textContent).toContain('正在创建副本')
    expect(mocks.push).not.toHaveBeenCalled()

    await act(async () => { resolve({ trip_id: 'trip-created-by-server' } as TravelTripV2) })
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-created-by-server')
    expect(container?.textContent).not.toContain('已保存成功')
  })

  it('复制 API 失败时留在模板预览并明确报错，不显示保存成功', async () => {
    mocks.copyTemplate.mockRejectedValue(new Error('服务端拒绝创建'))
    await renderPreview()
    const button = [...(container?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('复制为我的行程'))

    await act(async () => { button?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(mocks.push).not.toHaveBeenCalled()
    expect(container?.textContent).toContain('服务端拒绝创建')
    expect(container?.textContent).not.toContain('已保存成功')
  })
})
