import { act, createElement, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TravelConversationMessage } from '@/components/travel/useTravelConversation'
import type { TravelAIEditResponseV2, TravelTripV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({
  applyEdit: vi.fn(),
  getStatus: vi.fn(),
  getPending: vi.fn(),
  resolvePending: vi.fn(),
  cancelPending: vi.fn(),
  fetchTrip: vi.fn(),
  stream: vi.fn(),
}))

vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  applyTravelTripAIEditV2: mocks.applyEdit,
  fetchTravelTripAIEditStatusV2: mocks.getStatus,
  fetchTravelTripAIPendingResolutionV2: mocks.getPending,
  resolveTravelTripAIPendingResolutionV2: mocks.resolvePending,
  cancelTravelTripAIPendingResolutionV2: mocks.cancelPending,
  fetchTravelTripV2: mocks.fetchTrip,
  createTravelV2IdempotencyKey: () => 'op-key-0001',
}))

vi.mock('@/api/travel', () => ({ streamTravelPlanV2: mocks.stream }))

import TravelV2TripConversation from './TravelV2TripConversation'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const tripId = '00000000-0000-4000-8000-000000000001'
const conversationId = `travel-v2-trip-${tripId}`

function makeTrip(revision = 1): TravelTripV2 {
  return {
    trip_id: tripId, title: '杭州慢游', status: 'active', revision,
    source_template_id: null, source_template_version: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
    document: {
      schema_version: 2,
      brief: {
        origin: '上海', destination: '杭州', timezone: 'Asia/Shanghai', start_date: '2026-10-20', day_count: 1,
        travelers: { adults: 2, children: 0 }, budget: { amount: 3000, currency: 'CNY' },
        pace: 'relaxed', interests: [], requirements: [],
      },
      selections: [],
      days: [{
        day_id: `${tripId}-day`, date: '2026-10-20', title: '第一天', legs: [],
        items: [{
          item_id: `${tripId}-item`, kind: 'place', title: '西湖', start_time: '09:00', duration_min: 90,
          fixed_start: false, must_visit: false, locked: false, note: '',
          place: { place_id: `${tripId}-place`, name: '西湖', lat: 30.2, lng: 120.1, address: '杭州', facts: { verification: 'verified' } },
        }],
      }],
      arrangements: { lodgings: [], intercity_trains: [] },
      totals: { currency: 'CNY', estimated_cost: null, transit_min: 0, distance_m: null },
      health: { status: 'ok', issues: [] },
    } as unknown as TravelTripV2['document'],
  }
}

function response(key: string, status: TravelAIEditResponseV2['status'], extra: Partial<TravelAIEditResponseV2> = {}): TravelAIEditResponseV2 {
  return {
    status, saved: status === 'saved' || status === 'succeeded', trip_id: tripId,
    operation_key: key, base_revision: 1, ...extra,
  }
}

function Harness({
  initialMessages, onTripSaved, onRestoreRequest,
}: {
  initialMessages: TravelConversationMessage[]
  onTripSaved: (revision: number) => Promise<void> | void
  onRestoreRequest?: (revision: number) => void
}) {
  const [messages, setMessages] = useState(initialMessages)
  return <TravelV2TripConversation
    trip={makeTrip()}
    conversationId={conversationId}
    conversation={{ messages, setMessages, syncError: '' }}
    onTripSaved={onTripSaved}
    onRestoreRequest={onRestoreRequest}
  />
}

let mounted: Array<{ root: Root; container: HTMLDivElement }> = []

function mount(
  initialMessages: TravelConversationMessage[] = [],
  onTripSaved: (revision: number) => Promise<void> | void = vi.fn(),
  onRestoreRequest?: (revision: number) => void,
) {
  const container = document.createElement('div')
  const root = createRoot(container)
  mounted.push({ root, container })
  act(() => root.render(createElement(Harness, { initialMessages, onTripSaved, onRestoreRequest })))
  return { root, container, onTripSaved }
}

async function submit(container: HTMLDivElement, text: string) {
  const textarea = container.querySelector('textarea')!
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
  await act(async () => {
    setter?.call(textarea, text)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

async function settle() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
}

describe('Trip 绑定的 V2 旅游会话', () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset()
  })

  afterEach(() => {
    for (const page of mounted.splice(0)) act(() => page.root.unmount())
    vi.clearAllMocks()
  })

  it('只读行程问题通过现有 SSE 回答，不调用正式写入或刷新 Revision', async () => {
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'read_only'))
    mocks.stream.mockImplementation(async function* () {
      yield { event: 'done', data: { status: 'success', result: { status: 'success', final_answer: '第二天只有两项安排，节奏比较轻松。', itinerary: null } } }
    })
    const page = mount()

    await submit(page.container, '第二天会不会太赶？')

    expect(mocks.applyEdit).toHaveBeenCalledWith(tripId, expect.objectContaining({
      expected_revision: 1, user_message: '第二天会不会太赶？', conversation_id: conversationId,
    }), 'op-key-0001')
    expect(mocks.stream).toHaveBeenCalledOnce()
    expect(mocks.stream.mock.calls[0][0]).toContain('当前正式行程')
    expect(page.container.textContent).toContain('第二天只有两项安排')
    expect(page.onTripSaved).not.toHaveBeenCalled()
    expect(mocks.getStatus).not.toHaveBeenCalled()
  })

  it('明确恢复目标进入现有版本历史确认，不调用 AI 编辑或 SSE', async () => {
    const onRestoreRequest = vi.fn()
    const page = mount([], vi.fn(), onRestoreRequest)

    await submit(page.container, '把当前行程恢复到正式版本 v2')

    expect(page.container.textContent).toContain('已识别为恢复正式行程 v2')
    expect(mocks.applyEdit).not.toHaveBeenCalled()
    expect(mocks.stream).not.toHaveBeenCalled()
    expect(mocks.getStatus).not.toHaveBeenCalled()

    const openHistory = [...page.container.querySelectorAll('button')]
      .find((button) => button.textContent === '打开历史并确认恢复 v2')!
    await act(async () => openHistory.click())
    expect(onRestoreRequest).toHaveBeenCalledWith(2)
  })

  it('保留只读 SSE 的阶段和 Tool/Provider 结果状态供用户查看', async () => {
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'read_only'))
    mocks.stream.mockImplementation(async function* (_prompt: string, _conversationId: string, options: { clientRunId: string }) {
      const runId = options.clientRunId
      yield { event: 'stage.started', data: { run_id: runId, seq: 1, stage: '景点检索' } }
      yield { event: 'tool.started', data: { run_id: runId, seq: 2, tool: 'travel.search_poi', tool_call_id: 'poi-1' } }
      yield { event: 'tool.result', data: { run_id: runId, seq: 3, tool: 'travel.search_poi', tool_call_id: 'poi-1', status: 'degraded', user_message: '实时景点资料暂不可用', error: 'secret provider stack' } }
      yield { event: 'stage.finished', data: { run_id: runId, seq: 4, stage: '景点检索', status: 'completed' } }
      yield { event: 'done', data: { run_id: runId, seq: 5, status: 'success', result: { status: 'success', final_answer: '西湖附近景点可以按体力安排。', itinerary: null } } }
    })
    const page = mount()

    await submit(page.container, '西湖附近还有什么景点？')

    expect(page.container.textContent).toContain('西湖附近景点可以按体力安排')
    expect(page.container.textContent).toContain('本次处理过程 · 已完成')
    expect(page.container.textContent).toContain('景点检索 · 完成')
    expect(page.container.textContent).toContain('景点资料检索')
    expect(page.container.textContent).toContain('实时景点资料暂不可用')
    expect(page.container.textContent).not.toContain('secret provider stack')
    expect(page.onTripSaved).not.toHaveBeenCalled()
  })

  it('修改必须与权威操作账本和正式 Trip Revision 一致才显示已保存', async () => {
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'saved', { new_revision: 2 }))
    mocks.getStatus.mockImplementation(async (_id: string, key: string) => response(key, 'succeeded', { new_revision: 2 }))
    mocks.fetchTrip.mockResolvedValue(makeTrip(2))
    const page = mount()

    await submit(page.container, '把西湖改到上午十点')

    expect(mocks.applyEdit).toHaveBeenCalledOnce()
    expect(mocks.getStatus).toHaveBeenCalledWith(tripId, 'op-key-0001')
    expect(mocks.fetchTrip).toHaveBeenCalledWith(tripId)
    expect(page.onTripSaved).toHaveBeenCalledWith(2)
    expect(page.container.textContent).toContain('服务端操作账本已确认保存到 v2')
    expect(page.container.textContent).not.toContain('travel-v2-ai-edit:')
  })

  it('保存已确认但行程刷新失败时提供只读重试，不重放修改', async () => {
    const onTripSaved = vi.fn().mockRejectedValueOnce(new Error('history unavailable')).mockResolvedValue(undefined)
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'saved', { new_revision: 2 }))
    mocks.getStatus.mockImplementation(async (_id: string, key: string) => response(key, 'succeeded', { new_revision: 2 }))
    mocks.fetchTrip.mockResolvedValue(makeTrip(2))
    const page = mount([], onTripSaved)

    await submit(page.container, '把西湖改到上午十点')

    expect(page.container.textContent).toContain('重新核对正式行程')
    expect(page.container.querySelector('textarea')?.disabled).toBe(true)
    expect(mocks.applyEdit).toHaveBeenCalledOnce()

    const refresh = [...page.container.querySelectorAll('button')].find((button) => button.textContent?.includes('重新核对正式行程'))!
    await act(async () => {
      refresh.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(onTripSaved).toHaveBeenCalledTimes(2)
    expect(page.container.textContent).toContain('正式行程和版本历史已刷新')
    expect(mocks.applyEdit).toHaveBeenCalledOnce()
  })

  it('刷新后用原 operation_key 查询，不再次提交未知请求', async () => {
    mocks.getStatus.mockImplementation(async (_id: string, key: string) => response(key, 'succeeded', { new_revision: 2 }))
    mocks.fetchTrip.mockResolvedValue(makeTrip(2))
    const original: TravelConversationMessage = {
      id: 'turn-1:user', conversationId, turnId: 'turn-1', role: 'user',
      text: '把西湖改到上午十点\n[[travel-v2-ai-edit:op-key-0001:1]]',
    }
    const page = mount([original])
    await settle()

    expect(mocks.getStatus).toHaveBeenCalledWith(tripId, 'op-key-0001')
    expect(mocks.applyEdit).not.toHaveBeenCalled()
    expect(page.onTripSaved).toHaveBeenCalledWith(2)
    expect(page.container.textContent).toContain('已保存')
  })

  it('Pending 只显示服务端选项，用户确认后用稳定独立幂等键提交', async () => {
    const pending = {
      pending_id: 'pending-0001', trip_id: tripId, base_revision: 1,
      affected_item_ids: [`${tripId}-item`], conflict_codes: ['TIME_OVERLAP'],
      allowed_resolutions: [{ resolution_id: 'use_next_available_time', label: '移至 11:00', proposed_start_time: '11:00' }],
      expires_at: '2026-10-20T10:00:00Z', status: 'pending',
    }
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: pending,
    }))
    mocks.getPending.mockResolvedValue(response('op-key-0001', 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: pending,
      conflicts: [{ conflict_code: 'TIME_OVERLAP', message: '西湖与下一项安排时间重叠。', affected_item_ids: [`${tripId}-item`], allowed_resolutions: pending.allowed_resolutions }],
    }))
    mocks.resolvePending.mockResolvedValue(response('travel-v2-resolution-pending-0001-use_next_available_time', 'processing'))
    mocks.getStatus.mockImplementation(async (_id: string, key: string) => response(key, 'succeeded', { new_revision: 2 }))
    mocks.fetchTrip.mockResolvedValue(makeTrip(2))
    const page = mount()

    await submit(page.container, '把西湖改到上午十点')
    expect(page.container.textContent).toContain('西湖与下一项安排时间重叠')
    expect(page.container.textContent).toContain('西湖')
    expect(mocks.resolvePending).not.toHaveBeenCalled()

    const confirm = [...page.container.querySelectorAll('button')].find((button) => button.textContent?.includes('确认：移至 11:00'))!
    await act(async () => {
      confirm.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.resolvePending).toHaveBeenCalledWith(
      tripId, 'pending-0001', 'use_next_available_time',
      'travel-v2-resolution-pending-0001-use_next_available_time',
    )
    expect(page.onTripSaved).toHaveBeenCalledWith(2)
  })

  it('服务端没有允许的 Pending 选项时保持阻塞，只允许用户取消', async () => {
    const pending = {
      pending_id: 'pending-blocked', trip_id: tripId, base_revision: 1,
      affected_item_ids: [`${tripId}-item`], conflict_codes: ['TIME_OVERLAP'],
      allowed_resolutions: [], expires_at: '2026-10-20T10:00:00Z', status: 'pending',
    }
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: pending,
    }))
    mocks.getPending.mockResolvedValue(response('op-key-0001', 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: pending,
      conflicts: [{ conflict_code: 'TIME_OVERLAP', message: '两项安排时间重叠。', affected_item_ids: pending.affected_item_ids, allowed_resolutions: [] }],
    }))
    mocks.cancelPending.mockResolvedValue(response('op-key-0001', 'cancelled', { pending_id: pending.pending_id }))
    const page = mount()

    await submit(page.container, '把两个活动都安排在上午十点')

    expect(page.container.textContent).toContain('当前操作保持阻塞')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent?.includes('确认：'))).toBe(false)
    expect(mocks.resolvePending).not.toHaveBeenCalled()

    const cancel = [...page.container.querySelectorAll('button')].find((button) => button.textContent?.includes('取消修改'))!
    await act(async () => {
      cancel.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(mocks.cancelPending).toHaveBeenCalledWith(tripId, 'pending-blocked')
    expect(page.container.textContent).toContain('已取消待解决操作')
    expect(page.onTripSaved).not.toHaveBeenCalled()
  })

  it.each([
    { status: 'expired' as const, expected: '待解决操作已过期' },
    { status: 'cancelled' as const, expected: '待解决操作已取消' },
  ])('服务端 Pending 状态为 $status 时不展示旧确认选项', async ({ status, expected }) => {
    const pending = {
      pending_id: `pending-${status}`, trip_id: tripId, base_revision: 1,
      affected_item_ids: [`${tripId}-item`], conflict_codes: ['TIME_OVERLAP'],
      allowed_resolutions: [{ resolution_id: 'use_next_available_time', label: '移至 11:00' }],
      expires_at: '2026-10-20T10:00:00Z', status,
    }
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: { ...pending, status: 'pending' },
    }))
    mocks.getPending.mockResolvedValue(response('op-key-0001', status, {
      pending_id: pending.pending_id, pending_resolution: pending,
    }))
    const page = mount()

    await submit(page.container, '把西湖改到上午十点')

    expect(page.container.textContent).toContain(expected)
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent?.includes('确认：'))).toBe(false)
    expect(mocks.resolvePending).not.toHaveBeenCalled()
    expect(page.onTripSaved).not.toHaveBeenCalled()
  })

  it('已消费的 Pending 只提示核对原操作，不推断 Trip 已保存', async () => {
    const pending = {
      pending_id: 'pending-consumed', trip_id: tripId, base_revision: 1,
      affected_item_ids: [`${tripId}-item`], conflict_codes: ['TIME_OVERLAP'],
      allowed_resolutions: [{ resolution_id: 'use_next_available_time', label: '移至 11:00' }],
      expires_at: '2026-10-20T10:00:00Z', status: 'consumed',
    }
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'pending_resolution', {
      pending_id: pending.pending_id, pending_resolution: { ...pending, status: 'pending' },
    }))
    mocks.getPending.mockResolvedValue(response('op-key-0001', 'succeeded', {
      pending_id: pending.pending_id, pending_resolution: pending,
    }))
    const page = mount()

    await submit(page.container, '把西湖改到上午十点')

    expect(page.container.textContent).toContain('结果待核实')
    expect(page.container.textContent).toContain('正在核对原操作账本')
    expect(page.container.textContent).not.toContain('已保存')
    expect([...page.container.querySelectorAll('button')].some((button) => button.textContent?.includes('确认：'))).toBe(false)
    expect(mocks.getStatus).not.toHaveBeenCalled()
    expect(page.onTripSaved).not.toHaveBeenCalled()
  })

  it('UNSUPPORTED 明确拒绝整段重规划，不启动 planner SSE 或写入', async () => {
    mocks.applyEdit.mockImplementation(async (_id: string, _input: unknown, key: string) => response(key, 'unsupported', {
      message: '当前编辑入口只接受一项局部修改。',
    }))
    const page = mount()

    await submit(page.container, '把三天行程全部重新安排，再换城市')

    expect(page.container.textContent).toContain('当前编辑入口只接受一项局部修改。')
    expect(mocks.stream).not.toHaveBeenCalled()
    expect(mocks.getStatus).not.toHaveBeenCalled()
    expect(page.onTripSaved).not.toHaveBeenCalled()
  })
})
