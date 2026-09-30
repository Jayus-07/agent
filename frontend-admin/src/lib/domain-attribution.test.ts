import { describe, expect, it } from 'vitest'

import { orderDomainGraphs, subflowAttribution } from './domain-attribution'

describe('subflowAttribution', () => {
  it('子流图：拼「父域标签 · 子流 子流」', () => {
    expect(
      subflowAttribution({ domain: 'travel', domain_label: '旅游规划图执行', subflow: 'commerce' }),
    ).toBe('旅游规划图执行 · commerce 子流')
  })

  it('缺父域标签时回退到父域 route_mode', () => {
    expect(subflowAttribution({ domain: 'travel', domain_label: null, subflow: 'booking' })).toBe(
      'travel · booking 子流',
    )
  })

  it('顶级域图（后端下发 null）返回 null', () => {
    expect(subflowAttribution({ domain: null, domain_label: null, subflow: null })).toBeNull()
  })

  it('只有 domain 没有 subflow：数据半截，宁可不显示', () => {
    expect(subflowAttribution({ domain: 'travel', subflow: null })).toBeNull()
  })

  it('后端未下发新字段（旧响应）时返回 null，不臆造归属', () => {
    expect(subflowAttribution({})).toBeNull()
  })
})

describe('orderDomainGraphs', () => {
  const graph = (route_mode: string, domain: string | null) => ({ route_mode, domain })

  it('子流紧跟自己的父域（父域在前）', () => {
    const ordered = orderDomainGraphs([
      graph('travel_commerce', 'travel'),
      graph('travel', null),
      graph('travel_booking', 'travel'),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['travel', 'travel_commerce', 'travel_booking'])
  })

  it('多个父域各自成组，保持后端给的相对顺序', () => {
    const ordered = orderDomainGraphs([
      graph('travel_booking', 'travel'),
      graph('selection_funnel', null),
      graph('travel', null),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['selection_funnel', 'travel', 'travel_booking'])
  })

  it('长度恒等：不丢节点、不重复', () => {
    const input = [
      graph('customer_service', null),
      graph('travel_commerce', 'travel'),
      graph('travel', null),
      graph('travel_c_not_exist', 'travel'),
      graph('travel_booking', 'travel'),
    ]
    const ordered = orderDomainGraphs(input)
    expect(ordered).toHaveLength(input.length)
    expect(new Set(ordered.map((n) => n.route_mode))).toEqual(new Set(input.map((n) => n.route_mode)))
  })

  it('父域不在列表里的子流兜底追加到末尾（宁可排得难看，也不能消失）', () => {
    const ordered = orderDomainGraphs([
      graph('travel_commerce', 'travel'),
      graph('orphan_sub', 'not_in_list'),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['travel_commerce', 'orphan_sub'])
  })

  it('没有子流时原样返回（顶级域之间不重排）', () => {
    const input = [graph('customer_service', null), graph('travel', null)]
    expect(orderDomainGraphs(input).map((n) => n.route_mode)).toEqual([
      'customer_service',
      'travel',
    ])
  })
})
