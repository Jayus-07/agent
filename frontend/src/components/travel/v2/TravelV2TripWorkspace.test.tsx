import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({
  fetchTrip: vi.fn(),
  fetchRevisions: vi.fn(),
  applyEdit: vi.fn(),
  restoreTrip: vi.fn(),
  searchPanelProps: [] as Array<Record<string, any>>,
  tripRequests: [] as Array<Record<string, any>>,
  revisionRequests: [] as Array<Record<string, any>>,
}))

vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  fetchTravelTripV2: mocks.fetchTrip,
  fetchTravelTripRevisionsV2: mocks.fetchRevisions,
  applyTravelTripEditV2: mocks.applyEdit,
  restoreTravelTripV2: mocks.restoreTrip,
}))

vi.mock('./TravelSearchPanel', () => ({
  default: (props: Record<string, any>) => {
    mocks.searchPanelProps.push(props)
    return createElement('div', { 'data-testid': `search-panel-${props.trip?.trip_id}` }, `搜索面板 ${props.trip?.trip_id}`)
  },
}))

vi.mock('./TravelV2Map', () => ({
  default: ({ selectedLocation }: { selectedLocation: { name: string } | null }) =>
    createElement('div', { 'data-testid': 'map-selection' }, selectedLocation?.name ?? ''),
}))

vi.mock('./TravelV2EditDialog', () => ({
  default: ({ trip, onEdit }: { trip: TravelTripV2; onEdit: (operation: unknown, summary: string) => Promise<boolean> }) =>
    createElement('div', { 'data-testid': 'edit-dialog' }, [
      createElement('span', { key: 'trip' }, `编辑 ${trip.trip_id}`),
      createElement('button', {
        key: 'submit',
        type: 'button',
        onClick: () => void onEdit({ op: 'update_item', item_id: `${trip.trip_id}-item` }, `${trip.trip_id} 修改已保存`),
      }, `提交 ${trip.trip_id} 编辑`),
    ]),
}))

vi.mock('./TravelV2Frame', () => ({ TravelBottomNav: () => null }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import TravelV2TripWorkspace from './TravelV2TripWorkspace'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Deferred<T> {
  promise: Promise<T>
  resolve(value: T): void
  reject(reason: unknown): void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((accept, decline) => { resolve = accept; reject = decline })
  return { promise, resolve, reject }
}

function makeTrip(id: string, title: string, revision = 1): TravelTripV2 {
  return {
    trip_id: id,
    title,
    status: 'active',
    revision,
    source_template_id: null,
    source_template_version: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
    document: {
      schema_version: 2,
      brief: {
        origin: '福州', destination: title, timezone: 'Asia/Shanghai', start_date: '2026-10-20', day_count: 1,
        travelers: { adults: 2, children: 0 }, budget: { amount: 3000, currency: 'CNY' },
        pace: 'balanced', interests: [], requirements: [],
      },
      selections: [],
      days: [{
        day_id: `${id}-day`, date: '2026-10-20', title: `${title} 第一天`, legs: [],
        items: [{
          item_id: `${id}-item`, kind: 'place', title: `${title} 景点`, start_time: '09:00', duration_min: 90,
          fixed_start: false, must_visit: false, locked: false, note: '',
          place: { place_id: `${id}-place`, name: `${title} 景点`, lat: 30.2, lng: 120.1, address: `${title} 地址`, facts: { verification: 'verified' } },
        }],
      }],
      arrangements: { lodgings: [], intercity_trains: [] },
      totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
      health: { status: 'ok', issues: [] },
    },
  } as unknown as TravelTripV2
}

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
}

const mounted: Array<{ root: Root; container: HTMLDivElement }> = []

function mount(tripId: string) {
  const container = document.createElement('div')
  const root = createRoot(container)
  mounted.push({ root, container })
  act(() => root.render(createElement(TravelV2TripWorkspace, { tripId })))
  return {
    container,
    goTo(nextTripId: string) {
      act(() => root.render(createElement(TravelV2TripWorkspace, { tripId: nextTripId })))
    },
  }
}

function tripRequest(index: number) {
  return mocks.tripRequests[index] as { tripId: string; signal?: AbortSignal; deferred: Deferred<TravelTripV2> }
}

async function load(request: ReturnType<typeof tripRequest>, trip: TravelTripV2) {
  request.deferred.resolve(trip)
  await settle()
}

function latestSearchProps(tripId: string) {
  return [...mocks.searchPanelProps].reverse().find((props) => props.trip?.trip_id === tripId)!
}

async function click(container: HTMLElement, predicate: (button: HTMLButtonElement) => boolean) {
  const button = [...container.querySelectorAll('button')].find(predicate)
  if (!button) throw new Error('没有找到预期按钮')
  await act(async () => button.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

beforeEach(() => {
  mocks.searchPanelProps.length = 0
  mocks.tripRequests.length = 0
  mocks.revisionRequests.length = 0
  mocks.fetchTrip.mockReset().mockImplementation((tripId: string, options?: { signal?: AbortSignal }) => {
    const pending = deferred<TravelTripV2>()
    mocks.tripRequests.push({ tripId, signal: options?.signal, deferred: pending })
    return pending.promise
  })
  mocks.fetchRevisions.mockReset().mockImplementation(async (tripId: string, options?: { signal?: AbortSignal }) => {
    mocks.revisionRequests.push({ tripId, signal: options?.signal })
    return { trip_id: tripId, revisions: [] }
  })
  mocks.applyEdit.mockReset()
  mocks.restoreTrip.mockReset()
})

afterEach(() => {
  for (const { root, container } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

describe('V2 工作台 Trip 状态隔离', () => {
  it('A → B 时立即隐藏 A，B 的 GET 完成后只展示 B', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    expect(page.container.textContent).toContain('行程 A')

    page.goTo('trip-b')
    expect(page.container.textContent).toContain('正在读取 V2 行程')
    expect(page.container.textContent).not.toContain('行程 A')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B'))

    expect(page.container.textContent).toContain('行程 B')
    expect(page.container.textContent).not.toContain('行程 A')
    expect(tripRequest(0).signal?.aborted).toBe(true)
  })

  it.each([
    ['404', Object.assign(new Error('没有找到行程'), { status: 404 })],
    ['网络失败', new TypeError('Failed to fetch')],
  ])('A → B，B 返回%s 时不显示或允许编辑 A', async (_label, failure) => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    page.goTo('trip-b')
    const request = tripRequest(1)
    request.deferred.reject(failure)
    await settle()

    expect(page.container.textContent).toContain(failure instanceof Error ? failure.message : '')
    expect(page.container.textContent).not.toContain('行程 A')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent?.includes('编辑行程'))).toBe(false)
  })

  it('A 的 GET 晚于 B 返回时，不能覆盖当前 B', async () => {
    const page = mount('trip-a')
    const requestA = tripRequest(0)
    page.goTo('trip-b')
    const requestB = tripRequest(1)
    await load(requestB, makeTrip('trip-b', '行程 B'))
    await load(requestA, makeTrip('trip-a', '迟到的行程 A'))

    expect(page.container.textContent).toContain('行程 B')
    expect(page.container.textContent).not.toContain('迟到的行程 A')
    expect(requestA.signal?.aborted).toBe(true)
  })

  it('A 的 Revision 请求迟到时，不能给 B 增加撤销入口', async () => {
    mocks.fetchRevisions.mockImplementation((tripId: string, options?: { signal?: AbortSignal }) => {
      const pending = deferred<{ trip_id: string; revisions: Array<Record<string, unknown>> }>()
      mocks.revisionRequests.push({ tripId, signal: options?.signal, deferred: pending })
      return pending.promise
    })
    const page = mount('trip-a')
    const requestA = tripRequest(0)
    await load(requestA, makeTrip('trip-a', '行程 A', 2))
    const revisionA = mocks.revisionRequests[0] as { signal?: AbortSignal; deferred: Deferred<{ trip_id: string; revisions: Array<Record<string, unknown>> }> }

    page.goTo('trip-b')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B'))
    const revisionB = mocks.revisionRequests[1] as { deferred: Deferred<{ trip_id: string; revisions: Array<Record<string, unknown>> }> }
    revisionB.deferred.resolve({ trip_id: 'trip-b', revisions: [] })
    await settle()
    revisionA.deferred.resolve({ trip_id: 'trip-a', revisions: [{ revision: 2, parent_revision: 1, change_type: 'manual_edit' }] })
    await settle()

    expect(revisionA.signal?.aborted).toBe(true)
    expect(page.container.textContent).toContain('行程 B')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent?.trim() === '撤销')).toBe(false)
  })

  it('A 的编辑成功回执迟到时，不覆盖 B，也不在 B 显示保存/取消成功', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A')
    await load(tripRequest(0), tripA)
    const edit = deferred<{ saved: true; trip_id: string; revision: number; document: TravelTripV2['document']; status: 'active' }>()
    mocks.applyEdit.mockReturnValue(edit.promise)

    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    page.goTo('trip-b')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B'))
    edit.resolve({ saved: true, trip_id: 'trip-a', revision: 2, document: tripA.document, status: 'active' })
    await settle()

    expect(page.container.textContent).toContain('行程 B')
    expect(page.container.textContent).not.toContain('行程 A')
    expect(page.container.textContent).not.toContain('trip-a 修改已保存')
    expect(page.container.textContent).not.toContain('取消成功')
  })

  it('A 的 Restore 成功回执迟到时，不覆盖 B 或展示 A 的撤销提示', async () => {
    mocks.fetchRevisions.mockImplementation(async (tripId: string) => ({
      trip_id: tripId,
      revisions: tripId === 'trip-a' ? [{ revision: 2, parent_revision: 1, change_type: 'manual_edit' }] : [],
    }))
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A', 2)
    await load(tripRequest(0), tripA)
    const restore = deferred<{ saved: true; revision: number; document: TravelTripV2['document'] }>()
    mocks.restoreTrip.mockReturnValue(restore.promise)

    await click(page.container, (button) => button.textContent?.trim() === '撤销')
    page.goTo('trip-b')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B'))
    restore.resolve({ saved: true, revision: 3, document: tripA.document })
    await settle()

    expect(page.container.textContent).toContain('行程 B')
    expect(page.container.textContent).not.toContain('行程 A')
    expect(page.container.textContent).not.toContain('已撤销上一次保存')
    expect(page.container.textContent).not.toContain('取消成功')
  })

  it('A 的查询、地图选择和工具进度迟到时，不污染 B', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    await click(page.container, (button) => button.textContent?.trim() === '美食')
    const oldSearchProps = latestSearchProps('trip-a')
    act(() => {
      oldSearchProps.onLocationSelect?.({ name: 'A 的查询地点', lat: 30, lng: 120 })
      oldSearchProps.onSearchEvent?.({ queryId: 'query-a', kind: 'food', status: 'success', source: 'amap', message: 'A 的查询完成' })
    })
    expect(page.container.textContent).toContain('A 的查询地点')
    expect(page.container.textContent).toContain('A 的查询完成')

    page.goTo('trip-b')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B'))
    act(() => {
      oldSearchProps.onLocationSelect?.({ name: '迟到的 A 查询地点', lat: 31, lng: 121 })
      oldSearchProps.onSearchEvent?.({ queryId: 'query-a-late', kind: 'food', status: 'success', source: 'amap', message: '迟到的 A 工具进度' })
      oldSearchProps.onTripSaved?.({ ...makeTrip('trip-a', '行程 A', 2) }, 'A 选入已保存。')
    })

    expect(page.container.querySelector('[data-testid="map-selection"]')?.textContent).toBe('')
    expect(page.container.textContent).toContain('行程 B')
    expect(page.container.textContent).not.toContain('A 选入已保存')
    expect(page.container.textContent).not.toContain('A 的查询完成')
    expect(page.container.textContent).not.toContain('迟到的 A')
  })

  it('同一 queryId 的终态替换查询中状态，且不覆盖其他记录或已终止记录', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    await click(page.container, (button) => button.textContent?.trim() === '美食')
    const searchProps = latestSearchProps('trip-a')

    act(() => {
      searchProps.onSearchEvent?.({ queryId: 'query-one', kind: 'food', status: 'querying', source: 'amap', message: '查询一中' })
      searchProps.onSearchEvent?.({ queryId: 'query-two', kind: 'hotel', status: 'querying', source: 'amap', message: '查询二中' })
      searchProps.onSearchEvent?.({ queryId: 'query-one', kind: 'food', status: 'success', source: 'amap', message: '查询一成功' })
      searchProps.onSearchEvent?.({ queryId: 'query-two', kind: 'hotel', status: 'cancelled', source: 'amap', message: '查询二取消' })
      searchProps.onSearchEvent?.({ queryId: 'query-two', kind: 'hotel', status: 'success', source: 'amap', message: '迟到的查询二成功' })
    })

    const activity = page.container.querySelector('ol[aria-label="旅游查询运行记录"]')!
    const rows = [...activity.querySelectorAll('li')]
    expect(rows).toHaveLength(2)
    expect(rows.find((row) => row.textContent?.includes('查询一成功'))?.textContent).toContain('查询成功')
    expect(rows.find((row) => row.textContent?.includes('查询二取消'))?.textContent).toContain('已取消')
    expect(activity.textContent).not.toContain('查询中')
    expect(activity.textContent).not.toContain('迟到的查询二成功')
  })

  it('A → B → A 快速切换时，最终只显示最后一次 A 的读取状态', async () => {
    const page = mount('trip-a')
    const requestA1 = tripRequest(0)
    page.goTo('trip-b')
    const requestB = tripRequest(1)
    page.goTo('trip-a')
    const requestA2 = tripRequest(2)

    await load(requestA2, makeTrip('trip-a', '最后一次 A'))
    await load(requestB, makeTrip('trip-b', '迟到的 B'))
    await load(requestA1, makeTrip('trip-a', '最早的 A'))

    expect(page.container.textContent).toContain('最后一次 A')
    expect(page.container.textContent).not.toContain('迟到的 B')
    expect(page.container.textContent).not.toContain('最早的 A')
    expect(requestA1.signal?.aborted).toBe(true)
    expect(requestB.signal?.aborted).toBe(true)
  })
})
