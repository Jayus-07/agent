import { act, createElement, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchPublicShare: vi.fn(),
  downloadICS: vi.fn(),
  saveDownload: vi.fn(),
}))

vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  fetchPublicTravelTripShareV2: mocks.fetchPublicShare,
  downloadSharedTravelTripICSv2: mocks.downloadICS,
}))
vi.mock('./travelV2Download', () => ({ saveTravelICSDownload: mocks.saveDownload }))

import TravelV2PublicShare from './TravelV2PublicShare'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const SHARE_ID = 'a6d9b2a1-19f0-47e2-8b3d-4c57dd97da12'
const mounted: Array<{ root: Root; container: HTMLDivElement }> = []

function mount(strictMode = false) {
  const container = document.createElement('div')
  const root = createRoot(container)
  mounted.push({ root, container })
  const page = createElement(TravelV2PublicShare, { shareId: SHARE_ID })
  act(() => root.render(strictMode ? createElement(StrictMode, null, page) : page))
  return container
}

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
}

async function click(container: HTMLElement, label: string) {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent?.trim() === label)
  if (!button) throw new Error(`没有找到按钮：${label}`)
  await act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await settle()
}

const publicShare = {
  share_id: SHARE_ID,
  revision: 2,
  expires_at: '2026-12-01T12:00:00Z',
  title: '杭州慢游',
  brief: { destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-11-01', day_count: 1 },
  days: [{
    date: '2026-11-01',
    title: '西湖慢行',
    items: [{
      kind: 'place' as const,
      activity_type: null,
      title: '西湖',
      start_time: '09:00',
      duration_min: 90,
      place: { name: '西湖', address: '杭州市西湖区', lat: 30.2, lng: 120.1 },
    }],
    legs: [],
  }],
  arrangements: { lodgings: [], intercity_trains: [] },
}

beforeEach(() => {
  window.history.replaceState(window.history.state, '', `/travel/share/${SHARE_ID}#token=one-time-token`)
  mocks.fetchPublicShare.mockReset().mockResolvedValue(publicShare)
  mocks.downloadICS.mockReset().mockResolvedValue({ blob: new Blob(['BEGIN:VCALENDAR']), filename: '杭州.ics' })
  mocks.saveDownload.mockReset()
})

afterEach(() => {
  for (const { root, container } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  window.history.replaceState(window.history.state, '', '/')
})

describe('TravelV2PublicShare', () => {
  it('从 fragment 读取令牌后清除地址栏副本，只呈现白名单字段', async () => {
    const container = mount(true)
    await settle()

    expect(mocks.fetchPublicShare).toHaveBeenCalledWith(SHARE_ID, 'one-time-token')
    expect(window.location.hash).toBe('')
    expect(container.textContent).toContain('杭州慢游')
    expect(container.textContent).toContain('固定版本 v2')
    expect(container.textContent).toContain('西湖')
    expect(container.textContent).toContain('杭州市西湖区')
    expect(container.textContent).not.toContain('owner_id')

    await click(container, '下载此版本 ICS')
    expect(mocks.downloadICS).toHaveBeenCalledWith(SHARE_ID, 'one-time-token')
    expect(mocks.saveDownload).toHaveBeenCalledWith({ blob: expect.any(Blob), filename: '杭州.ics' })
  })

  it('缺少 fragment 令牌时拒绝读取分享 API', async () => {
    window.history.replaceState(window.history.state, '', `/travel/share/${SHARE_ID}`)
    const container = mount()
    await settle()

    expect(mocks.fetchPublicShare).not.toHaveBeenCalled()
    expect(container.textContent).toContain('无效、已撤销或已过期')
  })
})
