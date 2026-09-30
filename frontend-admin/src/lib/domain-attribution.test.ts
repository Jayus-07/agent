import { describe, expect, it } from 'vitest'

import { subflowAttribution } from './domain-attribution'

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
