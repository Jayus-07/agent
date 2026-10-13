import { beforeEach, describe, expect, it, vi } from 'vitest'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/api/client', () => ({ request }))

import {
  archiveTravelTripV2,
  applyTravelTripEditV2,
  createTravelTripV2,
  copyTravelTemplateV2,
  fetchTravelTemplateV2,
  fetchTravelTemplatesV2,
  fetchTravelTripRevisionsV2,
  fetchTravelTripsV2,
  fetchTravelTripV2,
  addTravelMealV2,
  restoreTravelTripV2,
  selectTravelLodgingV2,
  selectTravelTrainV2,
  searchTravelFoodV2,
  searchTravelHotelsV2,
  searchTravelPlacesV2,
  searchTravelTrainsV2,
  saveTravelTripDocumentV2,
} from './travelV2'

describe('旅游 V2 BFF API', () => {
  beforeEach(() => request.mockReset())

  it('首页与历史规划只请求 V2 路由', async () => {
    request.mockResolvedValue({ trips: [], templates: [] })

    await fetchTravelTripsV2()
    await fetchTravelTemplatesV2()
    await fetchTravelTemplateV2('杭州/慢游')
    await archiveTravelTripV2('trip-123')

    expect(request.mock.calls.map(([path]) => path)).toEqual([
      '/api/travel/v2/trips?limit=50',
      '/api/travel/v2/templates?limit=50',
      '/api/travel/v2/templates/%E6%9D%AD%E5%B7%9E%2F%E6%85%A2%E6%B8%B8',
      '/api/travel/v2/trips/trip-123',
    ])
  })

  it('行程刷新后按 V2 历史读取可撤销的上一版本', async () => {
    request.mockResolvedValue({ trip_id: 'trip/123', revisions: [] })

    await fetchTravelTripRevisionsV2('trip/123')

    expect(request).toHaveBeenCalledWith('/api/travel/v2/trips/trip%2F123/revisions')
  })

  it('Trip 与 Revision 只读请求可接收 AbortSignal', async () => {
    request.mockResolvedValue({ trip_id: 'trip-123', revisions: [] })
    const controller = new AbortController()

    await fetchTravelTripV2('trip-123', { signal: controller.signal })
    await fetchTravelTripRevisionsV2('trip-123', { signal: controller.signal })

    expect(request.mock.calls).toEqual([
      ['/api/travel/v2/trips/trip-123', { signal: controller.signal }],
      ['/api/travel/v2/trips/trip-123/revisions', { signal: controller.signal }],
    ])
  })

  it('美食、酒店和车次查询都调用独立 V2 Tool API', async () => {
    request.mockResolvedValue({ status: 'success', results: [] })

    await searchTravelFoodV2('杭州')
    await searchTravelHotelsV2('杭州')
    await searchTravelPlacesV2('杭州', '西湖')
    await searchTravelTrainsV2({
      from_station: '福州', to_station: '杭州', travel_date: '2026-12-01',
    })

    expect(request.mock.calls.map(([path]) => path)).toEqual([
      '/api/travel/v2/search/food',
      '/api/travel/v2/search/hotels',
      '/api/travel/v2/search/places',
      '/api/travel/v2/search/trains',
    ])
    expect(request.mock.calls.map(([, options]) => options)).toEqual([
      { method: 'POST', body: { city: '杭州' } },
      { method: 'POST', body: { city: '杭州' } },
      { method: 'POST', body: { city: '杭州', keyword: '西湖' } },
      { method: 'POST', body: { from_station: '福州', to_station: '杭州', travel_date: '2026-12-01' } },
    ])
  })

  it('只读 Provider 查询可接收取消信号', async () => {
    request.mockResolvedValue({ status: 'success', results: [] })
    const controller = new AbortController()

    await searchTravelFoodV2('杭州', { signal: controller.signal })

    expect(request).toHaveBeenCalledWith('/api/travel/v2/search/food', {
      method: 'POST', body: { city: '杭州' }, signal: controller.signal,
    })
  })

  it('新规划写入 V2 行程，并以版本 CAS 保存 Agent 后续修改', async () => {
    request.mockResolvedValue({ saved: true, revision: 2, document: {} })
    const document = { schema_version: 2 } as never

    await createTravelTripV2({ title: '杭州 3 天行程', document }, 'chat-run-1')
    await saveTravelTripDocumentV2('trip/123', {
      expected_revision: 1,
      command_type: 'ai_edit',
      change_summary: '根据对话调整行程',
      document,
    }, 'chat-run-2')

    expect(request.mock.calls).toEqual([
      ['/api/travel/v2/trips', {
        method: 'POST', body: { title: '杭州 3 天行程', document },
        headers: { 'Idempotency-Key': 'chat-run-1' },
      }],
      ['/api/travel/v2/trips/trip%2F123/document', {
        method: 'PUT',
        body: { expected_revision: 1, command_type: 'ai_edit', change_summary: '根据对话调整行程', document },
        headers: { 'Idempotency-Key': 'chat-run-2' },
      }],
    ])
  })

  it('结构化编辑只发送操作、基准 Revision 和幂等键', async () => {
    request.mockResolvedValue({ saved: true, revision: 5, document: {}, status: 'active' })
    const input = {
      expected_revision: 4,
      change_summary: '调整西湖停留时长',
      operation: { op: 'update_item', item_id: 'item-1', duration_min: 150 } as const,
    }

    await applyTravelTripEditV2('trip/123', input, 'edit-key-1')

    expect(request).toHaveBeenCalledWith('/api/travel/v2/trips/trip%2F123/edits', {
      method: 'POST', body: input, headers: { 'Idempotency-Key': 'edit-key-1' },
    })
  })

  it('模板复制可显式复用同一幂等键', async () => {
    request.mockResolvedValue({ trip_id: 'trip-created-once' })

    await copyTravelTemplateV2('hangzhou-slow', 'copy-intent-1')

    expect(request).toHaveBeenCalledWith('/api/travel/v2/templates/hangzhou-slow/copy', {
      method: 'POST', body: {}, headers: { 'Idempotency-Key': 'copy-intent-1' },
    })
  })

  it('编辑、选入与恢复重试可复用同一 Idempotency-Key 和原 payload', async () => {
    request.mockResolvedValue({ saved: true })
    const editInput = {
      expected_revision: 4,
      change_summary: '调整时长',
      operation: { op: 'update_item', item_id: 'item-1', duration_min: 120 } as const,
    }
    const mealInput = {
      expected_revision: 4, day_id: 'day-1', meal_period: 'lunch' as const,
      selection_id: 'meal-1', merchant: { merchant_id: 'merchant-1' } as never,
    }
    const lodgingInput = {
      expected_revision: 4, lodging: { selection_id: 'hotel-1' } as never,
    }
    const trainInput = {
      expected_revision: 4, train: { selection_id: 'train-1' } as never,
    }
    const restoreInput = { expected_revision: 4, target_revision: 2 }

    await applyTravelTripEditV2('trip/123', editInput, 'same-edit-key')
    await applyTravelTripEditV2('trip/123', editInput, 'same-edit-key')
    await addTravelMealV2('trip/123', mealInput, 'same-meal-key')
    await addTravelMealV2('trip/123', mealInput, 'same-meal-key')
    await selectTravelLodgingV2('trip/123', lodgingInput, 'same-lodging-key')
    await selectTravelLodgingV2('trip/123', lodgingInput, 'same-lodging-key')
    await selectTravelTrainV2('trip/123', trainInput, 'same-train-key')
    await selectTravelTrainV2('trip/123', trainInput, 'same-train-key')
    await restoreTravelTripV2('trip/123', restoreInput, 'same-restore-key')
    await restoreTravelTripV2('trip/123', restoreInput, 'same-restore-key')

    for (let index = 0; index < request.mock.calls.length; index += 2) {
      expect(request.mock.calls[index + 1]).toEqual(request.mock.calls[index])
    }
    expect(request.mock.calls.map(([, options]) => options.headers['Idempotency-Key'])).toEqual([
      'same-edit-key', 'same-edit-key', 'same-meal-key', 'same-meal-key',
      'same-lodging-key', 'same-lodging-key', 'same-train-key', 'same-train-key',
      'same-restore-key', 'same-restore-key',
    ])
  })
})
