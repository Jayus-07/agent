import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  confirmTravelPlan,
  fetchTravelPlanDiff,
  fetchTravelPlanVersions,
  reverseGeocodeTravelOrigin,
  restoreTravelPlan,
  streamTravelPlan,
} from './travel'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('旅游行程版本 API', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('查询版本历史时把会话 id 编码进路径', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ conversation_id: 'conv/1', versions: [] }),
    )

    await fetchTravelPlanVersions('conv/1')

    expect(String(fetchSpy.mock.calls[0][0])).toContain('/api/travel/plans/conv%2F1/versions')
  })

  it('旅游规划流透传真实 Tool 事件和最终结构化结果', async () => {
    const body = [
      'event: run.started',
      'data: {"event":"run.started","run_id":"run-1","seq":1}',
      '',
      'event: tool.started',
      'data: {"event":"tool.started","tool":"travel.search_poi","seq":2}',
      '',
      'event: tool.result',
      'data: {"event":"tool.result","tool":"travel.search_poi","status":"success","seq":3}',
      '',
      'event: done',
      'data: {"event":"done","status":"success","result":{"itinerary":{"plan_version":1}},"seq":4}',
      '',
    ].join('\n')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )

    const events = []
    for await (const event of streamTravelPlan('福州1天', 'conv-1')) {
      events.push(event)
    }

    expect(fetchSpy.mock.calls[0][0]).toBe('/api/travel/plan/stream')
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({
      message: '福州1天', session_id: 'conv-1', conversation_id: 'conv-1',
    })
    expect(events.map((event) => event.event)).toEqual([
      'run.started', 'tool.started', 'tool.result', 'done',
    ])
    expect(events.at(-1)?.data).toMatchObject({
      status: 'success', result: { itinerary: { plan_version: 1 } },
    })
  })

  it('差异查询携带 from/to 版本', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ from_version: 1, to_version: 2 }),
    )

    await fetchTravelPlanDiff('conv-1', 1, 2)

    expect(String(fetchSpy.mock.calls[0][0])).toContain(
      '/api/travel/plans/conv-1/diff?from_version=1&to_version=2',
    )
  })

  it('确认和恢复分别发送当前版本与 CAS 基准', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ status: 'ok' }),
    )

    await confirmTravelPlan('conv-1', 2)
    await restoreTravelPlan('conv-1', 1, 2)

    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({
      conversation_id: 'conv-1', plan_version: 2,
    })
    expect(JSON.parse(String(fetchSpy.mock.calls[1][1]?.body))).toEqual({
      conversation_id: 'conv-1', target_version: 1, base_version: 2,
    })
  })

  it('定位反查只返回城市字段，找不到城市时不伪造结果', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ found: true, city: '福州', district: '鼓楼区' }),
    )
    await expect(reverseGeocodeTravelOrigin(26.08, 119.30)).resolves.toEqual({
      city: '福州', label: '福州 · 鼓楼区',
    })

    vi.restoreAllMocks()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ found: false, result: null }))
    await expect(reverseGeocodeTravelOrigin(0, 0)).resolves.toBeNull()
  })
})
