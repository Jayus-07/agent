import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { TravelTemplateV2, TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ fetchTemplate: vi.fn(), copyTemplate: vi.fn(), createIdempotencyKey: vi.fn(), push: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  fetchTravelTemplateV2: mocks.fetchTemplate,
  copyTravelTemplateV2: mocks.copyTemplate,
  createTravelV2IdempotencyKey: mocks.createIdempotencyKey,
}))

import TravelTemplatePreview from './TravelTemplatePreview'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function template(slug = 'hangzhou-slow'): TravelTemplateV2 {
  const destination = slug === 'shanghai-weekend' ? '上海' : '杭州'
  return {
    slug, title: `${destination}湖山慢游`, destination, summary: '慢游路线',
    cover_image_url: null, tags: ['轻松'],
    content: { brief: { destination, day_count: 1, travelers: { adults: 2, children: 0 }, budget: { amount: null } }, days: [{ day_id: 'day-1', date: null, items: [], legs: [] }] } as unknown as TravelTemplateV2['content'],
  } as TravelTemplateV2
}

let root: Root | null = null
let container: HTMLDivElement | null = null

async function renderPreview(slug = 'hangzhou-slow') {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root?.render(createElement(TravelTemplatePreview, { slug })) })
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
}

async function clickCopyButton() {
  const button = [...(container?.querySelectorAll('button') ?? [])].find((item) =>
    item.textContent?.includes('复制为我的行程') || item.textContent?.includes('重试'),
  )
  await act(async () => { button?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
  return button
}

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
})

beforeEach(() => {
  mocks.fetchTemplate.mockReset().mockImplementation((slug: string) => Promise.resolve(template(slug)))
  mocks.copyTemplate.mockReset()
  mocks.createIdempotencyKey.mockReset().mockReturnValue('copy-intent-1')
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
    expect(mocks.copyTemplate).toHaveBeenCalledWith('hangzhou-slow', 'copy-intent-1')
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-created-by-server')
    expect(container?.textContent).not.toContain('已保存成功')
  })

  it('第一次服务端成功但响应丢失时，安全重试复用幂等键且只创建一份 Trip', async () => {
    const persistedTrips = new Map<string, TravelTripV2>()
    let createdTripCount = 0
    mocks.copyTemplate.mockImplementation((_slug: string, requestKey: string) => {
      const existingTrip = persistedTrips.get(requestKey)
      if (existingTrip) return Promise.resolve(existingTrip)

      createdTripCount += 1
      const createdTrip = { trip_id: 'trip-created-once' } as TravelTripV2
      persistedTrips.set(requestKey, createdTrip)
      return Promise.reject(new TypeError('Failed to fetch'))
    })
    await renderPreview()
    const button = [...(container?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('复制为我的行程'))!

    await act(async () => { button.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(container?.textContent).toContain('复制结果尚未确认')
    expect(container?.textContent).toContain('服务端可能已经创建行程')
    expect(button.textContent).toContain('安全重试（复用原请求）')
    expect(mocks.push).not.toHaveBeenCalled()

    await act(async () => { button.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(mocks.copyTemplate).toHaveBeenCalledTimes(2)
    expect(mocks.copyTemplate.mock.calls.map(([, requestKey]) => requestKey)).toEqual(['copy-intent-1', 'copy-intent-1'])
    expect(createdTripCount).toBe(1)
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-created-once')
    expect(container?.textContent).not.toContain('已保存成功')
  })

  it.each([
    [401, '登录状态已过期，请重新登录'],
    [403, '没有权限执行该操作，请联系管理员'],
    [404, '请求的资源不存在或已被删除'],
    [422, '提交内容不符合要求，请检查后重试'],
  ])('HTTP %s 明确拒绝时展示对应错误，不提示结果未知', async (status, message) => {
    mocks.copyTemplate.mockRejectedValue(new ApiError('拒绝复制', status))
    await renderPreview()

    const button = await clickCopyButton()

    expect(container?.textContent).toContain(message)
    expect(container?.textContent).not.toContain('服务端可能已经创建行程')
    expect(container?.textContent).not.toContain('复制结果尚未确认')
    expect(button?.textContent).toContain('使用原请求标识重试')
  })

  it('409 幂等冲突使用现有错误语义，重试仍复用原请求键', async () => {
    mocks.copyTemplate
      .mockRejectedValueOnce(new ApiError('幂等键冲突', 409, undefined, 'IDEMPOTENCY_CONFLICT'))
      .mockResolvedValueOnce({ trip_id: 'trip-replayed' } as TravelTripV2)
    await renderPreview()

    const button = await clickCopyButton()
    expect(container?.textContent).toContain('请求已处理或正在处理中')
    expect(container?.textContent).not.toContain('服务端可能已经创建行程')
    expect(button?.textContent).toContain('使用原请求标识重试')

    await act(async () => { button?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(mocks.copyTemplate.mock.calls.map(([, requestKey]) => requestKey)).toEqual(['copy-intent-1', 'copy-intent-1'])
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-replayed')
  })

  it.each([
    ['网络错误', new TypeError('Failed to fetch')],
    ['客户端超时', new ApiError('请求超时', 0, { code: 'TIMEOUT' }, 'TIMEOUT')],
    ['服务端异常', new ApiError('服务暂时不可用', 500)],
  ])('%s 时保留原键并提示结果未确认', async (_label, failure) => {
    mocks.copyTemplate.mockRejectedValueOnce(failure).mockResolvedValueOnce({ trip_id: 'trip-after-retry' } as TravelTripV2)
    await renderPreview()

    const button = await clickCopyButton()
    expect(container?.textContent).toContain('复制结果尚未确认')
    expect(container?.textContent).toContain('服务端可能已经创建行程')
    expect(button?.textContent).toContain('安全重试（复用原请求）')

    await act(async () => { button?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(mocks.copyTemplate.mock.calls.map(([, requestKey]) => requestKey)).toEqual(['copy-intent-1', 'copy-intent-1'])
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-after-retry')
  })

  it('复制途中切换模板后旧请求成功回执不导航或覆盖新模板', async () => {
    let resolveCopy!: (trip: TravelTripV2) => void
    mocks.copyTemplate
      .mockReturnValueOnce(new Promise((resolve) => { resolveCopy = resolve }))
      .mockResolvedValueOnce({ trip_id: 'trip-after-replay' } as TravelTripV2)
    await renderPreview()
    await clickCopyButton()

    await act(async () => {
      root?.render(createElement(TravelTemplatePreview, { slug: 'shanghai-weekend' }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(container?.textContent).toContain('上海湖山慢游')

    await act(async () => { resolveCopy({ trip_id: 'trip-from-old-template' } as TravelTripV2) })
    expect(mocks.push).not.toHaveBeenCalled()
    expect(container?.textContent).toContain('上海湖山慢游')
    expect(container?.textContent).not.toContain('杭州湖山慢游')

    await act(async () => {
      root?.render(createElement(TravelTemplatePreview, { slug: 'hangzhou-slow' }))
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const retryButton = [...(container?.querySelectorAll('button') ?? [])].find((item) => item.textContent?.includes('重试'))
    expect(retryButton).toBeDefined()
    await act(async () => { retryButton?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(mocks.copyTemplate.mock.calls.map(([, requestKey]) => requestKey)).toEqual(['copy-intent-1', 'copy-intent-1'])
    expect(mocks.push).toHaveBeenCalledWith('/travel/itineraries/trip-after-replay')
  })

  it('离开模板页面后不取消复制写请求，但忽略迟到回执', async () => {
    let resolveCopy!: (trip: TravelTripV2) => void
    mocks.copyTemplate.mockReturnValue(new Promise((resolve) => { resolveCopy = resolve }))
    await renderPreview()
    await clickCopyButton()

    act(() => { root?.unmount() })
    root = null
    await act(async () => { resolveCopy({ trip_id: 'trip-after-leaving' } as TravelTripV2) })

    expect(mocks.copyTemplate).toHaveBeenCalledTimes(1)
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
