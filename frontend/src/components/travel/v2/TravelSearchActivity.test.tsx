import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import TravelSearchActivity from './TravelSearchActivity'
import type { TravelSearchEvent } from './TravelSearchPanel'

function renderActivity(events: TravelSearchEvent[]) {
  const container = document.createElement('div')
  const root = createRoot(container)
  act(() => root.render(createElement(TravelSearchActivity, { events })))
  return { container, dispose: () => act(() => root.unmount()) }
}

describe('V2 实时查询状态', () => {
  it('展示真实查询中的数据源与状态，不生成额外运行步骤', () => {
    const events: TravelSearchEvent[] = [
      { kind: 'train', status: 'querying', source: '12306', message: '正在查询动车…' },
    ]

    const rendered = renderActivity(events)
    expect(rendered.container.textContent).toContain('正在查询动车…')
    expect(rendered.container.textContent).toContain('12306')
    expect(rendered.container.textContent).toContain('查询中')
    rendered.dispose()
  })

  it('区分无结果、上游不可用和网络超时', () => {
    const events: TravelSearchEvent[] = [
      { kind: 'food', status: 'no_results', source: 'amap', message: '没有找到美食结果。' },
      { kind: 'hotel', status: 'provider_unavailable', source: 'amap', message: '酒店数据源不可用。' },
      { kind: 'train', status: 'network_timeout', source: '12306', message: '网络超时。' },
    ]

    const rendered = renderActivity(events)
    expect(rendered.container.textContent).toContain('无结果')
    expect(rendered.container.textContent).toContain('数据源不可用')
    expect(rendered.container.textContent).toContain('网络超时')
    rendered.dispose()
  })
})
