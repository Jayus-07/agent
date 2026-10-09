import { describe, expect, it } from 'vitest'
import type { Itinerary, ItineraryDay, ItineraryItem } from '@/api/travel'
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

function scheduleItem(
  title: string, poiId: string | null, start: string, end: string,
  kind = 'visit',
): ItineraryItem {
  return {
    title, kind, start, end, minutes: 120, wait_minutes: 0, note: '',
    poi: poiId ? {
      poi_id: poiId, name: title, lat: 30.25, lng: 120.15,
      ticket_cny: 0,
    } : null,
  }
}

function scheduleDay(dayIndex: number, dayDate: string, items: ItineraryItem[]): ItineraryDay {
  return {
    day_index: dayIndex, day_date: dayDate, active_minutes: 120,
    transit_minutes: 30, cost_cny: 100, items,
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

    const previous = itinerary()
    const summary = buildChangeSummary(previous, next)
    expect(summary).toEqual({
      fromVersion: 1,
      toVersion: 2,
      briefFields: ['budget_cny', 'days'],
      added: [],
      removed: [],
      moved: [{ poiId: 'poi-west-lake', name: '西湖', fromDay: 1, toDay: 2 }],
      itemChanges: [{
        kind: 'moved', previousItem: previous.days[0].items[0], nextItem: next.days[0].items[0],
        previousDay: 1, nextDay: 2, certainty: 'identity', details: { timeChanged: false },
      }],
      budgetDelta: 420,
    })
  })

  it('把唯一同日、重叠时段的景点一删一增保守归为推断替换', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖景区', 'poi-west-lake', '09:00', '11:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('灵隐寺', 'poi-lingyin', '09:15', '11:15'),
    ])] })

    const summary = buildChangeSummary(previous, next)

    expect(summary.itemChanges).toMatchObject([{
      kind: 'replaced', certainty: 'inferred',
      previousItem: { title: '西湖景区' }, nextItem: { title: '灵隐寺' },
      previousDay: 1, nextDay: 1,
      details: { timeChanged: true, previousTime: '09:00–11:00', nextTime: '09:15–11:15' },
    }])
    expect(summary.added).toEqual([])
    expect(summary.removed).toEqual([])
  })

  it('多个未匹配景点不猜替换关系', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-a', '09:00', '10:00'),
      scheduleItem('灵隐寺', 'poi-b', '14:00', '15:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西溪湿地', 'poi-c', '09:00', '10:00'),
      scheduleItem('雷峰塔', 'poi-d', '14:00', '15:00'),
    ])] })

    const summary = buildChangeSummary(previous, next)

    expect(summary.itemChanges.map((change) => change.kind)).toEqual([
      'removed', 'removed', 'added', 'added',
    ])
  })

  it('同一景点同一天改时段只产生一条时间变化', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake', '10:00', '12:00'),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges).toMatchObject([{
      kind: 'time_changed', previousDay: 1, nextDay: 1,
      details: { timeChanged: true, previousTime: '09:00–11:00', nextTime: '10:00–12:00' },
    }])
  })

  it('同一景点跨天且改时段计为移动一条，并附带时间变化', () => {
    const previous = itinerary({ days: [
      scheduleDay(1, '2026-10-10', [scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00')]),
      scheduleDay(2, '2026-10-11', []),
    ] })
    const next = itinerary({ plan_version: 2, days: [
      scheduleDay(1, '2026-10-10', []),
      scheduleDay(2, '2026-10-11', [scheduleItem('西湖', 'poi-west-lake', '14:00', '16:00')]),
    ] })

    expect(buildChangeSummary(previous, next).itemChanges).toMatchObject([{
      kind: 'moved', previousDay: 1, nextDay: 2,
      details: { timeChanged: true, previousTime: '09:00–11:00', nextTime: '14:00–16:00' },
    }])
  })

  it('整体调整出发日期不把行程项目误报成跨天移动', () => {
    const previous = itinerary({ days: [
      scheduleDay(1, '2026-10-10', [scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00')]),
      scheduleDay(2, '2026-10-11', []),
    ] })
    const next = itinerary({
      plan_version: 2,
      brief: { ...itinerary().brief, start_date: '2026-10-17' },
      days: [
        scheduleDay(1, '2026-10-17', [scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00')]),
        scheduleDay(2, '2026-10-18', []),
      ],
    })

    expect(buildChangeSummary(previous, next).itemChanges).toEqual([])
  })

  it('无景点 ID 的同名项目跨天不推断身份移动', () => {
    const previous = itinerary({ days: [
      scheduleDay(1, '2026-10-10', [scheduleItem('西湖', null, '09:00', '11:00')]),
      scheduleDay(2, '2026-10-11', []),
    ] })
    const next = itinerary({ plan_version: 2, days: [
      scheduleDay(1, '2026-10-10', []),
      scheduleDay(2, '2026-10-11', [scheduleItem('西湖', null, '09:00', '11:00')]),
    ] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'added'])
  })

  it('非景点项目不参与推断替换', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('午餐', null, '12:00', '13:00', 'meal'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('晚餐', null, '12:05', '13:05', 'meal'),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'added'])
  })

  it('重复 POI ID 不作为唯一身份跨天匹配', () => {
    const previous = itinerary({ days: [
      scheduleDay(1, '2026-10-10', [
        scheduleItem('西湖', 'poi-west-lake', '09:00', '10:00'),
        scheduleItem('西湖夜游', 'poi-west-lake', '19:00', '20:00'),
      ]),
      scheduleDay(2, '2026-10-11', []),
    ] })
    const next = itinerary({ plan_version: 2, days: [
      scheduleDay(1, '2026-10-10', []),
      scheduleDay(2, '2026-10-11', [
        scheduleItem('西湖', 'poi-west-lake', '09:00', '10:00'),
        scheduleItem('西湖夜游', 'poi-west-lake', '19:00', '20:00'),
      ]),
    ] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'removed', 'added', 'added'])
  })

  it('非法或缺失时间不触发推断替换', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake', '09:00', '不确定'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('灵隐寺', 'poi-lingyin', '09:00', ''),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'added'])
  })

  it('替换推断必须满足开始时间差、重叠率和时长比例阈值', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00'),
    ])] })
    const startsTooLate = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('灵隐寺', 'poi-lingyin', '09:31', '11:00'),
    ])] })
    const littleOverlap = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('灵隐寺', 'poi-lingyin', '10:00', '12:00'),
    ])] })

    expect(buildChangeSummary(previous, startsTooLate).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'added'])
    expect(buildChangeSummary(previous, littleOverlap).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'added'])
  })

  it('唯一无 ID、同名同类型同日同时间项目作为未变化项匹配', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', null, '09:00', '11:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', null, '09:00', '11:00'),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges).toEqual([])
  })

  it('无 ID 的重复同名同槽项目不强行消歧', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', null, '09:00', '11:00'),
      scheduleItem('西湖', null, '09:00', '11:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', null, '09:00', '11:00'),
      scheduleItem('西湖', null, '09:00', '11:00'),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['removed', 'removed', 'added', 'added'])
  })

  it('稳定 ID 不同但名称和时段相同仍按严格时槽规则显示推断替换', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake-v1', '09:00', '11:00'),
    ])] })
    const next = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [
      scheduleItem('西湖', 'poi-west-lake-v2', '09:00', '11:00'),
    ])] })

    expect(buildChangeSummary(previous, next).itemChanges.map((change) => change.kind))
      .toEqual(['replaced'])
  })

  it('输入项目顺序变化不会改变事件顺序', () => {
    const previous = itinerary({ days: [scheduleDay(1, '2026-10-10', [])] })
    const added = [
      scheduleItem('雷峰塔', 'poi-leifeng', '14:00', '15:00'),
      scheduleItem('西溪湿地', 'poi-xixi', '09:00', '11:00'),
    ]
    const first = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', added)] })
    const reversed = itinerary({ plan_version: 2, days: [scheduleDay(1, '2026-10-10', [...added].reverse())] })
    const project = (value: Itinerary) => buildChangeSummary(previous, value).itemChanges
      .map((change) => `${change.kind}:${change.nextItem?.title}`)

    expect(project(first)).toEqual(project(reversed))
  })

  it('缺失 Active/Draft 部分字段时可预测地返回空差异', () => {
    const partial = { plan_version: 1 } as unknown as Itinerary

    expect(buildChangeSummary(partial, partial)).toMatchObject({
      fromVersion: 1, toVersion: 1, briefFields: [], itemChanges: [], budgetDelta: 0,
    })
  })

  it('变化事件计数不把预算字段差异混入项目事件', () => {
    const previous = itinerary()
    const next = itinerary({
      plan_version: 2,
      brief: { ...itinerary().brief, budget_cny: 3500 },
      days: [scheduleDay(1, '2026-10-10', [
        scheduleItem('西湖', 'poi-west-lake', '09:00', '11:00'),
      ])],
    })

    const summary = buildChangeSummary(previous, next)
    expect(summary.briefFields).toEqual(['budget_cny'])
    expect(summary.itemChanges).toEqual([])
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
  it('按 tool_call_id 归属同名 Tool 的结果，不覆盖另一调用', () => {
    let state = initialTravelProcess('run-calls')
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: {
        run_id: 'run-calls', seq: 1, tool: 'travel.search_poi',
        task_id: 'poi-first', tool_call_id: 'call-first',
      },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.started',
      data: {
        run_id: 'run-calls', seq: 2, tool: 'travel.search_poi',
        task_id: 'poi-second', tool_call_id: 'call-second',
      },
    })
    state = reduceTravelStreamEvent(state, {
      event: 'tool.result',
      data: {
        run_id: 'run-calls', seq: 3, tool: 'travel.search_poi',
        task_id: 'poi-first', tool_call_id: 'call-first', status: 'success',
      },
    })

    expect(state.tools).toEqual([
      {
        tool: 'travel.search_poi', taskId: 'poi-first',
        toolCallId: 'call-first', status: 'success',
      },
      {
        tool: 'travel.search_poi', taskId: 'poi-second',
        toolCallId: 'call-second', status: 'running',
      },
    ])
  })

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
