import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createShare: vi.fn(),
  createKey: vi.fn(),
  revokeShare: vi.fn(),
  downloadICS: vi.fn(),
  saveDownload: vi.fn(),
}))

vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  createTravelTripShareV2: mocks.createShare,
  createTravelV2IdempotencyKey: mocks.createKey,
  revokeTravelTripShareV2: mocks.revokeShare,
  downloadTravelTripRevisionICSv2: mocks.downloadICS,
}))
vi.mock('./travelV2Download', () => ({ saveTravelICSDownload: mocks.saveDownload }))

import TravelTripRevisionActions from './TravelTripRevisionActions'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const SHARE_ID = 'a6d9b2a1-19f0-47e2-8b3d-4c57dd97da12'
const mounted: Array<{ root: Root; container: HTMLDivElement }> = []

function mount() {
  const container = document.createElement('div')
  const root = createRoot(container)
  mounted.push({ root, container })
  act(() => root.render(createElement(TravelTripRevisionActions, { tripId: 'trip-1', revision: 4 })))
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

beforeEach(() => {
  window.sessionStorage.clear()
  mocks.createShare.mockReset()
  mocks.createKey.mockReset().mockReturnValue('stable-share-intent-key')
  mocks.revokeShare.mockReset().mockResolvedValue(undefined)
  mocks.downloadICS.mockReset().mockResolvedValue({ blob: new Blob(['BEGIN:VCALENDAR']), filename: '杭州.ics' })
  mocks.saveDownload.mockReset()
})

afterEach(() => {
  for (const { root, container } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  window.sessionStorage.clear()
})

describe('TravelTripRevisionActions', () => {
  it('按选定 Revision 创建、复制并撤销分享；会话存储不保存令牌', async () => {
    mocks.createShare.mockResolvedValue({
      share_id: SHARE_ID,
      trip_id: 'trip-1',
      revision: 4,
      expires_at: new Date(Date.now() + 86_400_000).toISOString(),
      created_at: new Date().toISOString(),
      share_url: `/travel/share/${SHARE_ID}#token=demo-share`,
    })
    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: clipboardWrite } })
    const container = mount()

    await click(container, '创建分享链接')
    expect(mocks.createShare).toHaveBeenCalledWith(
      'trip-1', { revision: 4, expires_in_days: 30 }, 'stable-share-intent-key',
    )
    const urlInput = container.querySelector('input[readonly]') as HTMLInputElement
    expect(urlInput.value).toContain(`/travel/share/${SHARE_ID}#token=demo-share`)
    expect(urlInput.value).not.toContain('?token=')
    const savedIntent = window.sessionStorage.getItem('travel-v2-share-intent:trip-1:4') || ''
    expect(savedIntent).toContain('stable-share-intent-key')
    expect(savedIntent).toContain(SHARE_ID)
    expect(savedIntent).not.toContain('demo-share')

    await click(container, '复制链接')
    expect(clipboardWrite).toHaveBeenCalledWith(urlInput.value)
    await click(container, '撤销链接')
    await click(container, '确认撤销')
    expect(mocks.revokeShare).toHaveBeenCalledWith('trip-1', SHARE_ID)
    expect(window.sessionStorage.getItem('travel-v2-share-intent:trip-1:4')).toBeNull()
    expect(container.textContent).toContain('原链接立即失效')
  })

  it('创建结果不明时用相同 payload 和幂等键安全重试', async () => {
    mocks.createShare
      .mockRejectedValueOnce(new TypeError('network lost'))
      .mockResolvedValueOnce({
        share_id: SHARE_ID,
        trip_id: 'trip-1',
        revision: 4,
        expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        created_at: new Date().toISOString(),
        share_url: `/travel/share/${SHARE_ID}#token=recovered-share`,
      })
    const container = mount()

    await click(container, '创建分享链接')
    expect(container.textContent).toContain('安全重试')
    await click(container, '安全重试')

    expect(mocks.createShare.mock.calls).toEqual([
      ['trip-1', { revision: 4, expires_in_days: 30 }, 'stable-share-intent-key'],
      ['trip-1', { revision: 4, expires_in_days: 30 }, 'stable-share-intent-key'],
    ])
    expect(container.querySelector('input[readonly]')).not.toBeNull()
  })

  it('ICS 下载固定请求当前 Revision 并使用服务端文件名', async () => {
    const container = mount()
    await click(container, '下载此版本 ICS')

    expect(mocks.downloadICS).toHaveBeenCalledWith('trip-1', 4)
    expect(mocks.saveDownload).toHaveBeenCalledWith({
      blob: expect.any(Blob), filename: '杭州.ics',
    })
  })
})
