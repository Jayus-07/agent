import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ searchPlaces: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  searchTravelPlacesV2: mocks.searchPlaces,
}))

import TravelV2EditDialog, { type TravelV2EditPanel } from './TravelV2EditDialog'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const trip = {
  trip_id: 'trip-1', title: '杭州两日', status: 'active', revision: 4,
  source_template_id: null, source_template_version: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
  document: {
    schema_version: 2,
    brief: {
      origin: '上海', destination: '杭州', timezone: 'Asia/Shanghai',
      start_date: '2026-10-20', day_count: 1,
      travelers: { adults: 2, children: 0 },
      budget: { amount: 3000, currency: 'CNY' }, pace: 'balanced',
      interests: [], requirements: [],
    },
    selections: [],
    days: [{
      day_id: 'day-1', date: '2026-10-20', title: '西湖慢游',
      items: [{
        item_id: 'item-1', kind: 'place', title: '西湖', start_time: '09:00',
        duration_min: 120, fixed_start: false, place: {
          place_id: 'west-lake', name: '西湖', lat: 30.24, lng: 120.15,
          address: '杭州市西湖区', facts: {
            opening_hours: null, ticket_price_cny: null, verification: 'unknown',
            source: 'tencent:lbs', observed_at: null,
          },
        }, must_visit: false, locked: false, note: '',
      }], legs: [],
    }],
    arrangements: { lodgings: [], intercity_trains: [] },
    totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
    health: { status: 'ok', issues: [] },
  },
} as unknown as TravelTripV2

function renderDialog({
  panel = { kind: 'add' },
  onEdit = vi.fn(async () => true),
}: {
  panel?: TravelV2EditPanel
  onEdit?: (operation: unknown, summary: string) => Promise<boolean>
} = {}) {
  const container = document.createElement('div')
  const root = createRoot(container)
  const close = vi.fn()
  act(() => root.render(createElement(TravelV2EditDialog, {
    panel, trip, dayId: 'day-1', item: panel?.kind === 'item' ? trip.document.days[0].items[0] : null,
    route: panel?.kind === 'transport' ? { fromItemId: panel.fromItemId, toItemId: panel.toItemId } : undefined,
    saving: false, error: '', onClose: close, onEdit: onEdit as never,
  })))
  return { container, close, dispose: () => act(() => root.unmount()), onEdit }
}

afterEach(() => vi.clearAllMocks())

describe('V2 正式行程结构化编辑面板', () => {
  it('真实地点检索成功后，只有用户选择候选项才提交写入', async () => {
    mocks.searchPlaces.mockResolvedValue({
      kind: 'places', status: 'success', source: 'tencent:lbs',
      queried_at: '2026-10-09T08:00:00+08:00', message: '地点检索完成。',
      disclosure: '营业时间和票价未知。',
      results: [{
        selection_id: 'tencent:lbs:selection-1', category: '风景名胜', source: 'tencent:lbs',
        queried_at: '2026-10-09T08:00:00+08:00',
        place: {
          place_id: 'poi-1', name: '断桥残雪', lat: 30.25, lng: 120.16,
          address: '杭州市西湖区',
          facts: { opening_hours: null, ticket_price_cny: null, verification: 'unknown', source: 'tencent:lbs', observed_at: '2026-10-09T08:00:00+08:00' },
        },
      }],
    })
    const rendered = renderDialog()

    await act(async () => [...rendered.container.querySelectorAll('button')].find((button) => button.textContent?.includes('搜索真实地点'))?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const input = rendered.container.querySelector<HTMLInputElement>('#travel-v2-place-keyword')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '断桥')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      rendered.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(rendered.container.textContent).toContain('断桥残雪')
    expect(rendered.container.textContent).toContain('营业时间和价格未知')
    expect(rendered.onEdit).not.toHaveBeenCalled()
    await act(async () => {
      [...rendered.container.querySelectorAll('button')].find((button) => button.textContent?.includes('断桥残雪'))?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })
    expect(rendered.onEdit).toHaveBeenCalledWith(expect.objectContaining({ op: 'add_place', selection_id: 'tencent:lbs:selection-1' }), expect.any(String))
    expect(rendered.close).toHaveBeenCalledOnce()
    rendered.dispose()
  })

  it('展示提供方无结果，而不是把无结果说成查询失败', async () => {
    mocks.searchPlaces.mockResolvedValue({
      kind: 'places', status: 'no_results', source: 'tencent:lbs',
      queried_at: '2026-10-09T08:00:00+08:00', message: '查询成功，但没有匹配地点。',
      disclosure: '营业时间和票价未知。', results: [],
    })
    const rendered = renderDialog()
    await act(async () => [...rendered.container.querySelectorAll('button')].find((button) => button.textContent?.includes('搜索真实地点'))?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const input = rendered.container.querySelector<HTMLInputElement>('#travel-v2-place-keyword')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '没有匹配')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      rendered.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })
    expect(rendered.container.textContent).toContain('查询成功，但没有匹配地点')
    expect(rendered.onEdit).not.toHaveBeenCalled()
    rendered.dispose()
  })

  it('时间修改提交结构化操作，不在本地先展示保存成功', async () => {
    const onEdit = vi.fn(async () => false)
    const rendered = renderDialog({ panel: { kind: 'item', itemId: 'item-1' }, onEdit })
    await act(async () => [...rendered.container.querySelectorAll('button')].find((button) => button.textContent?.includes('时间与停留'))?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const timeInput = rendered.container.querySelector<HTMLInputElement>('input[type="time"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(timeInput, '10:30')
      timeInput.dispatchEvent(new Event('input', { bubbles: true }))
      rendered.container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ op: 'update_item', item_id: 'item-1', start_time: '10:30' }), expect.any(String))
    expect(rendered.container.textContent).not.toContain('已自动保存')
    expect(rendered.close).not.toHaveBeenCalled()
    rendered.dispose()
  })
})
