import { describe, expect, it } from 'vitest'

import { orderDomainGraphs, subflowAttribution } from './domain-attribution'

// 2026-11-01 收编重构：商务/预订两子流域图壳删除，实注册子流清零；
// 排序/归属逻辑本身是通用机制，用例改用假想子流名验证机制不回退。

describe('subflowAttribution', () => {
  it('子流图：拼「父域标签 · 子流 子流」', () => {
    expect(
      subflowAttribution({ domain: 'travel', domain_label: '旅游规划图执行', subflow: 'tour' }),
    ).toBe('旅游规划图执行 · tour 子流')
  })

  it('缺父域标签时回退到父域 route_mode', () => {
    expect(subflowAttribution({ domain: 'travel', domain_label: null, subflow: 'tour' })).toBe(
      'travel · tour 子流',
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
      graph('travel_tour', 'travel'),
      graph('travel', null),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['travel', 'travel_tour'])
  })

  it('多个父域各自成组，保持后端给的相对顺序', () => {
    const ordered = orderDomainGraphs([
      graph('travel_tour', 'travel'),
      graph('selection_funnel', null),
      graph('travel', null),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['selection_funnel', 'travel', 'travel_tour'])
  })

  it('长度恒等：不丢节点、不重复', () => {
    const input = [
      graph('customer_service', null),
      graph('travel', null),
      graph('travel_c_not_exist', 'travel'),
      graph('travel_tour', 'travel'),
    ]
    const ordered = orderDomainGraphs(input)
    expect(ordered).toHaveLength(input.length)
    expect(new Set(ordered.map((n) => n.route_mode))).toEqual(new Set(input.map((n) => n.route_mode)))
  })

  it('父域不在列表里的子流兜底追加到末尾（宁可排得难看，也不能消失）', () => {
    const ordered = orderDomainGraphs([
      graph('travel_tour', 'travel'),
      graph('orphan_sub', 'not_in_list'),
    ])
    expect(ordered.map((n) => n.route_mode)).toEqual(['travel_tour', 'orphan_sub'])
  })

  it('没有子流时原样返回（顶级域之间不重排）', () => {
    const input = [graph('customer_service', null), graph('travel', null)]
    expect(orderDomainGraphs(input).map((n) => n.route_mode)).toEqual([
      'customer_service',
      'travel',
    ])
  })
})
