import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import TravelSearchActivity from './TravelSearchActivity'
import type { TravelSearchEvent } from './TravelSearchPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function renderActivity(events: TravelSearchEvent[]) {
  const container = document.createElement('div')
  const root = createRoot(container)
  act(() => root.render(createElement(TravelSearchActivity, { events })))
  return { container, dispose: () => act(() => root.unmount()) }
}

describe('V2 查询活动状态', () => {
  it('查询中只显示查询类型，不提前声称 Provider 来源或查询时间', () => {
    const events: TravelSearchEvent[] = [
      { queryId: 'query-train-1', kind: 'train', status: 'querying', message: '正在查询动车…' },
    ]

    const rendered = renderActivity(events)
    expect(rendered.container.textContent).toContain('正在查询动车…')
    expect(rendered.container.textContent).toContain('查询中')
    expect(rendered.container.textContent).not.toContain('12306')
    rendered.dispose()
  })

  it('后端终态展示响应中的实际来源和查询时间', () => {
    const rendered = renderActivity([{
      queryId: 'query-train-success',
      kind: 'train',
      status: 'success',
      source: 'rail-source',
      queriedAt: '2026-10-12T03:04:00Z',
      message: '查询完成。',
    }])

    expect(rendered.container.textContent).toContain('查询完成。')
    expect(rendered.container.textContent).toContain('rail-source')
    expect(rendered.container.textContent).toContain(new Date('2026-10-12T03:04:00Z').toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }))
    expect(rendered.container.querySelector('svg')?.getAttribute('class')).not.toContain('animate-spin')
    rendered.dispose()
  })

  it('区分无结果、业务失败、超时、Provider 不可用、未启用和请求错误', () => {
    const events: TravelSearchEvent[] = [
      { queryId: 'query-food-1', kind: 'food', status: 'no_results', source: 'actual-source', message: '没有找到美食结果。' },
      { queryId: 'query-business-failure', kind: 'food', status: 'business_failure', message: '业务查询失败。' },
      { queryId: 'query-hotel-1', kind: 'hotel', status: 'provider_unavailable', message: '酒店数据源不可用。' },
      { queryId: 'query-train-2', kind: 'train', status: 'network_timeout', message: '网络超时。' },
      { queryId: 'query-disabled', kind: 'train', status: 'disabled', message: '动车查询未启用。' },
      { queryId: 'query-request-error', kind: 'hotel', status: 'request_error', message: '请求未完成。' },
    ]

    const rendered = renderActivity(events)
    expect(rendered.container.textContent).toContain('无结果')
    expect(rendered.container.textContent).toContain('查询失败')
    expect(rendered.container.textContent).toContain('数据源不可用')
    expect(rendered.container.textContent).toContain('网络超时')
    expect(rendered.container.textContent).toContain('能力未启用')
    expect(rendered.container.textContent).toContain('请求未完成')
    expect(rendered.container.textContent).toContain('actual-source')
    expect(rendered.container.querySelectorAll('.animate-spin')).toHaveLength(0)
    rendered.dispose()
  })

  it('用稳定 queryId 表示取消终态', () => {
    const rendered = renderActivity([
      { queryId: 'query-cancelled', kind: 'hotel', status: 'cancelled', message: '查询已取消。' },
    ])
    expect(rendered.container.textContent).toContain('已取消')
    expect(rendered.container.textContent).not.toContain('amap')
    expect(rendered.container.querySelector('svg')?.getAttribute('class')).not.toContain('animate-spin')
    rendered.dispose()
  })
})
