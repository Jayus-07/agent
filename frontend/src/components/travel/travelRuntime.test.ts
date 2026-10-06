import { describe, expect, it } from 'vitest'
import type { Itinerary } from '@/api/travel'
import {
  buildChangeSummary,
  classifyFact,
  costAvailability,
  acceptTravelRunEvent,
  initialTravelProcess,
  reduceTravelStreamEvent,
  travelProcessStatusLabel,
  type TravelRunState,
} from './travelRuntime'

function itinerary(overrides: Partial<Itinerary> = {}): Itinerary {
  return {
    brief: {
      destination: '杭州', origin: '上海', start_date: '2026-10-10', days: 2,
      party_size: 2, budget_cny: 3000, preferences: [], must_go: [], avoid: [],
      pace: 'moderate', diet: '', lodging: '', transport: '',
    },
    days: [
      {
        day_index: 1, day_date: '2026-10-10', active_minutes: 120,
        transit_minutes: 30, cost_cny: 100,
        items: [{
          title: '西湖', kind: 'visit', start: '09:00', end: '11:00',
          minutes: 120, wait_minutes: 0, note: '',
          poi: { poi_id: 'poi-west-lake', name: '西湖', lat: 30.25, lng: 120.15, ticket_cny: 0 },
        }],
      },
    ],
    cost: { tickets: 0, meals: 200, lodging: 600, transit: 100 },
    status: 'ready', plan_version: 1, warnings: [],
    ...overrides,
  }
}

describe('classifyFact', () => {
  it('把 live/official 来源标为已核实，把 seed/estimate 标为估算', () => {
    expect(classifyFact({ source: 'tencent:route' })).toBe('verified')
    expect(classifyFact({ source: 'official:hangzhou' })).toBe('verified')
    expect(classifyFact({ source: 'seed:local' })).toBe('estimated')
    expect(classifyFact({ source: 'estimate:local' })).toBe('estimated')
  })

  it('未核实字段优先显示待核实，缺来源字段显示未知', () => {
    expect(classifyFact({ source: 'tencent:poi', verification_status: 'unverified' })).toBe('unverified')
    expect(classifyFact({})).toBe('unknown')
  })
})

describe('costAvailability', () => {
  it('本地种子和估算通勤不得向用户展示参考金额', () => {
    const data = costAvailability(itinerary())
    expect(data.tickets).toBe('unavailable')
    expect(data.meals).toBe('unavailable')
    expect(data.lodging).toBe('unavailable')
    expect(data.transit).toBe('unavailable')
    expect(data.total).toBe('unavailable')
  })
})

describe('buildChangeSummary', () => {
  it('输出版本、必去字段、地点移动和预算变化，且顺序稳定', () => {
    const next = itinerary({
      plan_version: 2,
      brief: {
        ...itinerary().brief,
        days: 3,
        budget_cny: 3500,
      },
      cost: { tickets: 0, meals: 300, lodging: 900, transit: 120 },
      days: [{
        day_index: 2, day_date: '2026-10-11', active_minutes: 120,
        transit_minutes: 30, cost_cny: 100,
        items: [{
          title: '西湖', kind: 'visit', start: '09:00', end: '11:00',
          minutes: 120, wait_minutes: 0, note: '',
          poi: { poi_id: 'poi-west-lake', name: '西湖', lat: 30.25, lng: 120.15, ticket_cny: 0 },
        }],
      }],
    })

    expect(buildChangeSummary(itinerary(), next)).toEqual({
      fromVersion: 1,
      toVersion: 2,
      briefFields: ['budget_cny', 'days'],
      added: [],
      removed: [],
      moved: [{ poiId: 'poi-west-lake', name: '西湖', fromDay: 1, toDay: 2 }],
      budgetDelta: 420,
    })
  })
})

describe('acceptTravelRunEvent', () => {
  it('拒绝旧 run 和重复 seq，避免迟到结果覆盖新行程', () => {
    const initial: TravelRunState = { runId: 'run-new', lastSeq: 2, status: 'running' }
    const stale = acceptTravelRunEvent(initial, { runId: 'run-old', seq: 99, status: 'completed' })
    const duplicate = acceptTravelRunEvent(initial, { runId: 'run-new', seq: 2, status: 'completed' })
    const fresh = acceptTravelRunEvent(initial, { runId: 'run-new', seq: 3, status: 'completed' })

    expect(stale).toBe(initial)
    expect(duplicate).toBe(initial)
    expect(fresh).toEqual({ runId: 'run-new', lastSeq: 3, status: 'completed' })
  })
})

describe('reduceTravelStreamEvent', () => {
  it('展示需求理解和实时 Tool 返回的卡片预览', () => {
    let state = initialTravelProcess('run-brief')
    state = reduceTravelStreamEvent(state, {
      event: 'requirement.interpreted',
      data: {
        run_id: 'run-brief', seq: 1,
        brief: { destination: '福州', days: 2 },
        assumptions: ['未提供出发日期'], missing: [],
      },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-brief', seq: 2, tool: 'map_merchant_search_tool' },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.result',
      data: {
        run_id: 'run-brief', seq: 3, tool: 'map_merchant_search_tool',
        status: 'success', category: 'food', data_status: 'available',
        result_count: 1, preview: [{ name: '真实小吃店', source: 'amap' }],
      },
    })

    expect(state.requirement?.brief.destination).toBe('福州')
    expect(state.tools[0].category).toBe('food')
    expect(state.tools[0].preview?.[0]).toEqual({ name: '真实小吃店', source: 'amap' })
  })

  it('Tool 失败只标记该行，不把整轮 run 拖成 error（Tool Failure ≠ Workflow Failure）', () => {
    let state = initialTravelProcess('run-1')
    state = reduceTravelStreamEvent(state, {
      event: 'stage.started',
      data: { run_id: 'run-1', seq: 1, stage: 'travel_poi_expert' },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-1', seq: 2, tool: 'travel.search_poi' },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.result',
      data: {
        run_id: 'run-1', seq: 3, tool: 'travel.search_poi',
        status: 'failed', error_type: 'ProviderTimeout',
      },
    })

    expect(state.status).toBe('running')
    expect(state.tools).toEqual([{
      tool: 'travel.search_poi', status: 'failed', errorType: 'ProviderTimeout',
    }])
    expect(state.stages.travel_poi_expert.status).toBe('running')
  })

  it('降级 Tool 显示 degraded 并给用户可读说明，整轮保持正常', () => {
    let state = initialTravelProcess('run-degraded')
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-degraded', seq: 1, tool: 'travel_train_search_tool' },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.result',
      data: {
        run_id: 'run-degraded', seq: 2, tool: 'travel_train_search_tool',
        status: 'degraded', data_status: 'unavailable',
        user_message: '12306 实时查询暂时不可用，已继续生成行程',
      },
    })

    expect(state.status).toBe('running')
    expect(state.tools[0].status).toBe('degraded')
    expect(state.tools[0].userMessage).toBe('12306 实时查询暂时不可用，已继续生成行程')
  })

  it('只有 BLOCKED（硬依赖终止）才把整轮标记为终止', () => {
    let state = initialTravelProcess('run-blocked')
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-blocked', seq: 1, tool: 'travel_train_search_tool' },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.result',
      data: {
        run_id: 'run-blocked', seq: 2, tool: 'travel_train_search_tool',
        status: 'blocked', user_message: '无法验证满足交通约束的实时班次',
      },
    })

    expect(state.status).toBe('error')
    expect(state.tools[0].status).toBe('blocked')
  })

  it('拒绝旧 run 和乱序 seq，不让迟到事件覆盖当前过程', () => {
    let state = initialTravelProcess('run-new')
    state = reduceTravelStreamEvent(state, {
      event: 'stage.started',
      data: { run_id: 'run-new', seq: 2, stage: 'travel_poi_expert' },
    })
    const stale = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-old', seq: 99, tool: 'travel.search_poi' },
    })
    const duplicate = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: { run_id: 'run-new', seq: 2, tool: 'travel.search_poi' },
    })

    expect(stale).toBe(state)
    expect(duplicate).toBe(state)
  })

  it('把服务端 run.finished 的失败状态保留为错误', () => {
    const state = reduceTravelStreamEvent(initialTravelProcess('run-1'), {
      event: 'run.finished',
      data: { run_id: 'run-1', seq: 1, status: 'failed' },
    })

    expect(state.status).toBe('error')
  })

  it('把缺槽位追问视为正常返回而不是执行失败', () => {
    const state = reduceTravelStreamEvent(initialTravelProcess('run-clarify'), {
      event: 'done',
      data: { run_id: 'run-clarify', seq: 1, status: 'needs_clarification' },
    })

    expect(state.status).toBe('completed')
  })
})

describe('travelProcessStatusLabel', () => {
  it('把正常流式执行显示为进行中，不误报为中断', () => {
    expect(travelProcessStatusLabel({ loading: true, stopped: false, status: 'running' })).toBe('正在处理')
    expect(travelProcessStatusLabel({ loading: false, stopped: false, status: 'running' })).toBe('进行中')
    expect(travelProcessStatusLabel({ loading: false, stopped: true, status: 'running' })).toBe('已停止')
    expect(travelProcessStatusLabel({ loading: false, stopped: false, status: 'completed' })).toBe('已返回结果')
  })
})
