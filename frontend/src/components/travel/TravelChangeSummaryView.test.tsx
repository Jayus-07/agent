import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import TravelChangeSummaryView from './TravelChangeSummaryView'
import type { ItineraryItem } from '@/api/travel'
import type { TravelChangeSummary } from './travelRuntime'

let root: Root
let container: HTMLDivElement

function item(title: string): ItineraryItem {
  return {
    title, kind: 'visit', start: '09:00', end: '11:00', minutes: 120,
    wait_minutes: 0, note: '', poi: { poi_id: title, name: title, lat: 0, lng: 0, ticket_cny: 0 },
  }
}

function summary(): TravelChangeSummary {
  const previous = item('西湖景区')
  const next = item('灵隐寺')
  return {
    fromVersion: 12, toVersion: 13, briefFields: ['budget_cny'], added: ['wetland'],
    removed: ['old-stop'], moved: [], budgetDelta: 200,
    itemChanges: [
      { kind: 'replaced', certainty: 'inferred', previousItem: previous, nextItem: next,
        previousDay: 1, nextDay: 1, details: { timeChanged: false, previousTime: '09:00–11:00', nextTime: '09:00–11:00' } },
      { kind: 'added', nextItem: item('西溪湿地'), nextDay: 1, details: {} },
      { kind: 'removed', previousItem: item('河坊街'), previousDay: 1, details: {} },
      { kind: 'moved', previousItem: item('断桥'), nextItem: item('断桥'), previousDay: 1,
        nextDay: 2, details: { timeChanged: true, previousTime: '09:00–11:00', nextTime: '14:00–16:00' } },
      { kind: 'time_changed', previousItem: item('雷峰塔'), nextItem: item('雷峰塔'), previousDay: 1,
        nextDay: 1, details: { timeChanged: true, previousTime: '09:00–11:00', nextTime: '10:00–12:00' } },
    ],
  }
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove()
})

it('按唯一主分类显示新增、删除、推断替换、移动及附加时段变化', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChangeSummaryView summary={summary()} />))

  expect(container.textContent).toContain('新增 1')
  expect(container.textContent).toContain('删除 1')
  expect(container.textContent).toContain('可能替换 1')
  expect(container.textContent).toContain('移动 1')
  expect(container.textContent).toContain('时段调整 1')
  expect(container.textContent).toContain('西湖景区 → 灵隐寺')
  expect(container.textContent).toContain('同时调整时段')
})

it('紧凑模式默认最多展示三项，展开后显示完整差异', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChangeSummaryView summary={summary()} compact />))

  expect(container.textContent).toContain('查看全部 5 条变化')
  expect(container.textContent).not.toContain('雷峰塔')
  const button = container.querySelector('button')!
  await act(async () => button.click())
  expect(container.textContent).toContain('雷峰塔')
  expect(container.textContent).toContain('收起变化')
})

it('紧凑模式的需求字段变化即使条目不超过三项也可展开查看', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const value = summary()
  value.itemChanges = value.itemChanges.slice(0, 1)
  value.briefFields = ['budget_cny']

  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChangeSummaryView summary={value} compact />))

  expect(container.textContent).not.toContain('需求字段：budget_cny')
  const button = container.querySelector('button')
  expect(button?.textContent).toBe('查看需求字段')
  await act(async () => button?.click())
  expect(container.textContent).toContain('需求字段：budget_cny')
})
