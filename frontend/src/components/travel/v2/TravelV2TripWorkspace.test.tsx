import { act, createElement, useRef, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import type { TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({
  fetchTrip: vi.fn(),
  fetchRevisions: vi.fn(),
  applyEdit: vi.fn(),
  restoreTrip: vi.fn(),
  selectionWrite: vi.fn(),
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
    const intentRef = useRef<{ tripId: string; signature: string; key: string; input: { expected_revision: number; selection_id: string } } | null>(null)
    const [unknown, setUnknown] = useState(false)
    if (!intentRef.current && props.trip) {
      intentRef.current = {
        tripId: props.trip.trip_id,
        signature: `${props.trip.trip_id}:selection:v${props.trip.revision}`,
        key: `${props.trip.trip_id}-stable-selection-key`,
        input: { expected_revision: props.trip.revision, selection_id: `${props.trip.trip_id}-merchant` },
      }
    }
    mocks.searchPanelProps.push(props)
    const sendSelection = async () => {
      const intent = intentRef.current
      if (!intent || props.onTripWriteTransition?.({ kind: 'selection', ...intent, status: 'VALIDATING' }) === false) return
      props.onTripWriteTransition?.({ kind: 'selection', ...intent, status: 'SAVING' })
      try {
        const result = await mocks.selectionWrite(intent.tripId, intent.input, intent.key)
        props.onTripWriteTransition?.({ kind: 'selection', ...intent, status: 'SAVED' })
        props.onTripSaved?.({ ...props.trip, revision: result.revision, document: result.document }, '选入已保存。')
        setUnknown(false)
      } catch {
        props.onTripWriteTransition?.({ kind: 'selection', ...intent, status: 'UNKNOWN' })
        setUnknown(true)
      }
    }
    return createElement('div', { 'data-testid': `search-panel-${props.trip?.trip_id}` }, [
      createElement('span', { key: 'label' }, `搜索面板 ${props.trip?.trip_id}`),
      createElement('button', { key: 'select', type: 'button', disabled: props.writeLocked && !unknown, onClick: () => void sendSelection() }, '模拟选入操作'),
      unknown && createElement('button', { key: 'retry', type: 'button', onClick: () => void sendSelection() }, '使用同一请求安全重试'),
    ])
  },
}))

vi.mock('./TravelV2Map', () => ({
  default: ({ selectedLocation, selectedItemId, onSelectItem }: {
    selectedLocation: { name: string } | null
    selectedItemId: string
    onSelectItem: (id: string) => void
  }) => createElement('div', null, [
    createElement('div', {
      key: 'selection',
      'data-testid': 'map-selection',
      'data-selected-item-id': selectedItemId,
    }, selectedLocation?.name ?? ''),
    createElement('button', { key: 'saved', type: 'button', onClick: () => onSelectItem('trip-a-item') }, '定位已保存行程地点'),
  ]),
}))

vi.mock('./TravelV2EditDialog', () => ({
  default: ({ trip, onEdit, onClose, writeStatus, writeMessage, onRetry, onRefreshConflict }: {
    trip: TravelTripV2
    onEdit: (operation: unknown, summary: string) => Promise<boolean>
    onClose: () => void
    writeStatus?: string | null
    writeMessage?: string
    onRetry?: () => Promise<boolean>
    onRefreshConflict?: () => Promise<boolean>
  }) =>
    createElement('div', { 'data-testid': 'edit-dialog' }, [
      createElement('span', { key: 'trip' }, `编辑 ${trip.trip_id}`),
      writeStatus && createElement('p', { key: 'status', role: writeStatus === 'UNKNOWN' || writeStatus === 'CONFLICT' ? 'alert' : 'status' }, `${writeStatus} ${writeMessage}`),
      createElement('button', {
        key: 'submit',
        type: 'button',
        onClick: () => void onEdit({ op: 'update_item', item_id: `${trip.trip_id}-item` }, `${trip.trip_id} 修改已保存`),
      }, `提交 ${trip.trip_id} 编辑`),
      createElement('button', { key: 'close', type: 'button', onClick: onClose }, '关闭编辑面板'),
      writeStatus === 'UNKNOWN' && createElement('button', { key: 'retry', type: 'button', onClick: () => void onRetry?.() }, '使用同一请求安全重试'),
      writeStatus === 'CONFLICT' && createElement('button', { key: 'refresh', type: 'button', onClick: () => void onRefreshConflict?.() }, '刷新最新版本并重新编辑'),
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

async function clickWorkspaceTab(container: HTMLElement, label: string) {
  const nav = container.querySelector('nav[aria-label="行程与查询"]')
  if (!nav) throw new Error('没有找到工作台主 Tab')
  await click(container, (button) => nav.contains(button) && button.textContent?.trim() === label)
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
  mocks.selectionWrite.mockReset()
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

  it('编辑结果未知时保留正式 Revision，安全重试复用同键和相同 payload', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A')
    await load(tripRequest(0), tripA)
    mocks.applyEdit
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ saved: true, trip_id: 'trip-a', revision: 2, document: tripA.document, status: 'active' })

    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    await settle()

    expect(page.container.textContent).toContain('UNKNOWN')
    expect(page.container.textContent).toContain('v1')
    expect(page.container.textContent).not.toContain('已保存到 v2')
    const firstRequest = mocks.applyEdit.mock.calls[0]
    await click(page.container, (button) => button.textContent === '使用同一请求安全重试')
    await settle()

    expect(mocks.applyEdit).toHaveBeenCalledTimes(2)
    expect(mocks.applyEdit.mock.calls[1]).toEqual(firstRequest)
    expect(page.container.textContent).toContain('v2')
    expect(page.container.textContent).toContain('已保存')
  })

  it('成功刷新冲突后退出冲突状态并显示重新编辑提示', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    mocks.applyEdit.mockRejectedValueOnce(new ApiError('Revision changed', 409, undefined, 'VERSION_CONFLICT'))

    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    await settle()
    expect(page.container.textContent).toContain('CONFLICT')

    await click(page.container, (button) => button.textContent === '刷新最新版本并重新编辑')
    await load(tripRequest(1), makeTrip('trip-a', '行程 A 最新', 2))

    expect(page.container.textContent).toContain('已载入服务器最新 Revision v2；原修改未自动重放，请重新编辑。')
    expect(page.container.textContent).not.toContain('版本冲突')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent === '刷新最新版本并重新编辑')).toBe(false)
    expect(page.container.querySelector('[role="status"]')?.textContent).toContain('原修改未自动重放')
  })

  it('选入结果 UNKNOWN 时锁定工作台 Tab，并只允许同一选入请求重试', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A')
    await load(tripRequest(0), tripA)
    mocks.selectionWrite
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ revision: 2, document: tripA.document })
    const searchNav = page.container.querySelector('nav[aria-label="行程与查询"]')!
    await click(page.container, (button) => searchNav.contains(button) && button.textContent?.trim() === '美食')
    await click(page.container, (button) => button.textContent === '模拟选入操作')
    await settle()

    expect(page.container.textContent).toContain('当前查询 Tab 和其他正式写入已锁定')
    expect([...searchNav.querySelectorAll('button')].every((button) => button.disabled)).toBe(true)
    const hotelTab = [...searchNav.querySelectorAll('button')].find((button) => button.textContent?.trim() === '酒店')!
    await click(page.container, (button) => button === hotelTab)
    expect(hotelTab.getAttribute('aria-pressed')).toBe('false')
    const originalRequest = mocks.selectionWrite.mock.calls[0]
    await click(page.container, (button) => button.textContent === '使用同一请求安全重试')
    await settle()

    expect(mocks.selectionWrite).toHaveBeenCalledTimes(2)
    expect(mocks.selectionWrite.mock.calls[1]).toEqual(originalRequest)
    expect([...searchNav.querySelectorAll('button')].every((button) => !button.disabled)).toBe(true)
    const mobileUndo = page.container.querySelector<HTMLButtonElement>('button[aria-label="撤销最近一次正式变更"]')
    expect(mobileUndo).not.toBeNull()
    expect(mobileUndo?.disabled).toBe(false)
    await click(page.container, (button) => searchNav.contains(button) && button.textContent?.trim() === '酒店')
    expect([...searchNav.querySelectorAll('button')].find((button) => button.textContent?.trim() === '酒店')?.getAttribute('aria-pressed')).toBe('true')
    expect(mocks.applyEdit).not.toHaveBeenCalled()
    expect(mocks.restoreTrip).not.toHaveBeenCalled()
  })

  it('选入处于 SAVING 时锁定工作台 Tab 并保留当前候选', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A')
    const write = deferred<{ revision: number; document: TravelTripV2['document'] }>()
    mocks.selectionWrite.mockReturnValue(write.promise)
    await load(tripRequest(0), tripA)
    const searchNav = page.container.querySelector('nav[aria-label="行程与查询"]')!
    await clickWorkspaceTab(page.container, '美食')
    act(() => latestSearchProps('trip-a').onLocationSelect?.({ name: '保存中的候选', lat: 30.3, lng: 120.2 }))

    await click(page.container, (button) => button.textContent === '模拟选入操作')
    expect(page.container.textContent).toContain('本行程正在选入')
    expect([...searchNav.querySelectorAll('button')].every((button) => button.disabled)).toBe(true)
    expect([...searchNav.querySelectorAll('button')].find((button) => button.textContent?.trim() === '美食')?.getAttribute('aria-pressed')).toBe('true')
    expect(page.container.querySelector('[data-testid="map-selection"]')?.textContent).toBe('保存中的候选')

    write.resolve({ revision: 2, document: tripA.document })
    await settle()
    expect([...searchNav.querySelectorAll('button')].every((button) => !button.disabled)).toBe(true)
  })

  it('UNKNOWN 编辑期间禁止开始 Restore', async () => {
    const tripA = makeTrip('trip-a', '行程 A', 2)
    mocks.fetchRevisions.mockResolvedValue({
      trip_id: 'trip-a',
      revisions: [
        { trip_id: 'trip-a', revision: 2, parent_revision: 1, snapshot: tripA.document, change_type: 'manual_edit', change_summary: '编辑 v2', actor_id: 'user-a', created_at: '2026-10-10T10:00:00Z' },
        { trip_id: 'trip-a', revision: 1, parent_revision: null, snapshot: tripA.document, change_type: 'create', change_summary: '创建行程', actor_id: 'user-a', created_at: '2026-10-09T10:00:00Z' },
      ],
    })
    mocks.applyEdit.mockRejectedValue(new TypeError('Failed to fetch'))
    const page = mount('trip-a')
    await load(tripRequest(0), tripA)
    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    await settle()
    expect(page.container.textContent).toContain('UNKNOWN')
    await click(page.container, (button) => button.textContent === '关闭编辑面板')
    await click(page.container, (button) => button.getAttribute('aria-label') === '查看版本历史')
    await settle()

    const restoreButton = [...page.container.querySelectorAll('li')]
      .find((item) => item.textContent?.includes('版本 v1'))?.querySelector('button')
    expect(restoreButton?.disabled).toBe(true)
    expect(page.container.textContent).toContain('编辑请求结果待确认')
    expect(mocks.restoreTrip).not.toHaveBeenCalled()
  })

  it('v3 → v1 Restore UNKNOWN 后不接受 v2，也不允许编辑；重复确认不会重发', async () => {
    const tripA = makeTrip('trip-a', '行程 A', 3)
    mocks.fetchRevisions.mockResolvedValue({
      trip_id: 'trip-a',
      revisions: [
        { trip_id: 'trip-a', revision: 3, parent_revision: 2, snapshot: tripA.document, change_type: 'manual_edit', change_summary: '当前 v3', actor_id: 'user-a', created_at: '2026-10-11T10:00:00Z' },
        { trip_id: 'trip-a', revision: 2, parent_revision: 1, snapshot: tripA.document, change_type: 'manual_edit', change_summary: '中间 v2', actor_id: 'user-a', created_at: '2026-10-10T10:00:00Z' },
        { trip_id: 'trip-a', revision: 1, parent_revision: null, snapshot: tripA.document, change_type: 'create', change_summary: '初始 v1', actor_id: 'user-a', created_at: '2026-10-09T10:00:00Z' },
      ],
    })
    mocks.restoreTrip.mockRejectedValue(new TypeError('Failed to fetch'))
    const page = mount('trip-a')
    await load(tripRequest(0), tripA)
    await click(page.container, (button) => button.getAttribute('aria-label') === '查看版本历史')
    await settle()
    await click(page.container, (button) => button.textContent === '恢复此版本' && button.closest('li')?.textContent?.includes('版本 v1') === true)
    await click(page.container, (button) => button.textContent === '确认恢复 v1')
    await settle()

    expect(mocks.restoreTrip).toHaveBeenCalledOnce()
    expect(mocks.restoreTrip.mock.calls[0][1]).toEqual({ expected_revision: 3, target_revision: 1 })
    expect(page.container.textContent).toContain('确认将内容恢复到 v1')
    const v2Restore = [...page.container.querySelectorAll('li')]
      .find((item) => item.textContent?.includes('版本 v2'))?.querySelector('button')
    expect(v2Restore?.disabled).toBe(true)
    if (v2Restore) await act(async () => { v2Restore.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(mocks.restoreTrip).toHaveBeenCalledOnce()
    expect([...page.container.querySelectorAll('button')].find((button) => button.textContent?.includes('编辑行程'))?.disabled).toBe(true)
    expect([...page.container.querySelectorAll('button')].find((button) => button.textContent === '确认恢复 v1')?.disabled).toBe(true)

    const repeat = deferred<{ saved: true; trip_id: string; revision: number; document: TravelTripV2['document'] }>()
    mocks.restoreTrip.mockReset().mockReturnValue(repeat.promise)
    const page2 = mount('trip-b')
    await load(tripRequest(1), makeTrip('trip-b', '行程 B', 3))
    mocks.fetchRevisions.mockResolvedValue({
      trip_id: 'trip-b',
      revisions: [
        { trip_id: 'trip-b', revision: 3, parent_revision: 2, snapshot: tripA.document, change_type: 'manual_edit', change_summary: '当前 v3', actor_id: 'user-a', created_at: '2026-10-11T10:00:00Z' },
        { trip_id: 'trip-b', revision: 1, parent_revision: null, snapshot: tripA.document, change_type: 'create', change_summary: '初始 v1', actor_id: 'user-a', created_at: '2026-10-09T10:00:00Z' },
      ],
    })
    await click(page2.container, (button) => button.getAttribute('aria-label') === '查看版本历史')
    await settle()
    await click(page2.container, (button) => button.textContent === '恢复此版本')
    const confirm = [...page2.container.querySelectorAll('button')].find((button) => button.textContent === '确认恢复 v1')!
    await act(async () => {
      confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(mocks.restoreTrip).toHaveBeenCalledOnce()
    repeat.resolve({ saved: true, trip_id: 'trip-b', revision: 4, document: tripA.document })
    await settle()
  })

  it('409 保持原 Revision，显式刷新后不自动重放冲突编辑', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A')
    await load(tripRequest(0), tripA)
    mocks.applyEdit.mockRejectedValue(new ApiError('行程版本已变化', 409, null, 'VERSION_CONFLICT'))

    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    await settle()

    expect(page.container.textContent).toContain('CONFLICT')
    expect(page.container.textContent).toContain('v1')
    await click(page.container, (button) => button.textContent === '刷新最新版本并重新编辑')
    await settle()
    const latestRequest = tripRequest(1)
    await load(latestRequest, makeTrip('trip-a', '服务器最新行程', 2))

    expect(page.container.textContent).toContain('服务器最新行程')
    expect(page.container.textContent).not.toContain('编辑 trip-a')
    expect(mocks.applyEdit).toHaveBeenCalledOnce()
  })

  it('422 服务端校验失败保持正式 Revision 不变，也不显示已保存', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    mocks.applyEdit.mockRejectedValue(new ApiError('行程时间冲突', 422))

    await click(page.container, (button) => button.textContent?.includes('编辑行程') ?? false)
    await click(page.container, (button) => button.textContent === '提交 trip-a 编辑')
    await settle()

    expect(page.container.textContent).toContain('INVALID')
    expect(page.container.textContent).toContain('v1')
    expect(page.container.textContent).toContain('服务端校验未通过')
    expect(page.container.textContent).not.toContain('已保存到 v2')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent === '使用同一请求安全重试')).toBe(false)
  })

  it('历史面板展示版本摘要，恢复前确认并请求服务端追加 Revision', async () => {
    const page = mount('trip-a')
    const tripA = makeTrip('trip-a', '行程 A', 2)
    mocks.fetchRevisions.mockImplementation(async (tripId: string) => ({
      trip_id: tripId,
      revisions: [
        { trip_id: tripId, revision: 2, parent_revision: 1, snapshot: tripA.document, change_type: 'manual_edit', change_summary: '延长西湖停留', actor_id: 'user-a', created_at: '2026-10-10T10:00:00Z' },
        { trip_id: tripId, revision: 1, parent_revision: null, snapshot: tripA.document, change_type: 'create', change_summary: '创建行程', actor_id: 'user-a', created_at: '2026-10-09T10:00:00Z' },
      ],
    }))
    mocks.restoreTrip.mockResolvedValue({ saved: true, trip_id: 'trip-a', revision: 3, document: tripA.document })
    await load(tripRequest(0), tripA)

    await click(page.container, (button) => button.getAttribute('aria-label') === '查看版本历史')
    await settle()
    expect(page.container.querySelector('[role="dialog"]')?.textContent).toContain('延长西湖停留')
    await click(page.container, (button) => button.textContent === '恢复此版本')
    expect(page.container.textContent).toContain('确认将内容恢复到 v1')
    await click(page.container, (button) => button.textContent === '确认恢复 v1')
    await settle()

    expect(mocks.restoreTrip).toHaveBeenCalledOnce()
    expect(mocks.restoreTrip.mock.calls[0][1]).toEqual({ expected_revision: 2, target_revision: 1 })
    expect(mocks.restoreTrip.mock.calls[0][2]).toMatch(/^[0-9a-f-]{36}$/i)
    expect(page.container.textContent).toContain('v3')
    expect(page.container.textContent).toContain('新增 v3')
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

  it('选择临时查询候选时取消正式行程选择，回到地图行程点时恢复同步', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    const map = page.container.querySelector<HTMLDivElement>('[data-testid="map-selection"]')!
    expect(map.getAttribute('data-selected-item-id')).toBe('trip-a-item')

    await click(page.container, (button) => button.textContent?.trim() === '美食')
    const searchProps = latestSearchProps('trip-a')
    act(() => searchProps.onLocationSelect?.({ name: '临时餐饮候选', lat: 30.2, lng: 120.1, verification: 'estimated' }))

    expect(map.textContent).toContain('临时餐饮候选')
    expect(map.getAttribute('data-selected-item-id')).toBe('')
    expect(page.container.textContent).toContain('临时餐饮候选')

    await click(page.container, (button) => button.textContent === '定位已保存行程地点')
    expect(map.textContent).not.toContain('临时餐饮候选')
    expect(map.getAttribute('data-selected-item-id')).toBe('trip-a-item')
  })

  it('选择候选后切换到行程或其他查询 Tab 时清除候选并恢复正式地点选择', async () => {
    const page = mount('trip-a')
    await load(tripRequest(0), makeTrip('trip-a', '行程 A'))
    const map = page.container.querySelector<HTMLDivElement>('[data-testid="map-selection"]')!

    await clickWorkspaceTab(page.container, '美食')
    act(() => latestSearchProps('trip-a').onLocationSelect?.({ name: '美食临时候选', lat: 30.3, lng: 120.2 }))
    expect(map.textContent).toBe('美食临时候选')
    expect(map.getAttribute('data-selected-item-id')).toBe('')

    await clickWorkspaceTab(page.container, '行程')
    expect(map.textContent).toBe('')
    expect(map.getAttribute('data-selected-item-id')).toBe('trip-a-item')
    expect(page.container.textContent).toContain('行程 A 景点')

    await clickWorkspaceTab(page.container, '美食')
    act(() => latestSearchProps('trip-a').onLocationSelect?.({ name: '第二个临时候选', lat: 30.4, lng: 120.3 }))
    expect(map.textContent).toBe('第二个临时候选')
    await clickWorkspaceTab(page.container, '酒店')

    expect(map.textContent).toBe('')
    expect(map.getAttribute('data-selected-item-id')).toBe('trip-a-item')
    expect(page.container.textContent).toContain('行程 A 景点')
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
