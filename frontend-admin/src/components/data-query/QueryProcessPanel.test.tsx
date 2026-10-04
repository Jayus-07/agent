import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import QueryProcessPanel from './QueryProcessPanel'
import type { SSEStreamEvent } from '@/lib/types'

function mount(events: SSEStreamEvent[]): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<QueryProcessPanel events={events} />))
  return { root, container }
}

afterEach(() => {
  document.body.innerHTML = ''
})

it('展示需求理解、SQL 校验和 Tool 调用的实时阶段', () => {
  const { root, container } = mount([
    { event: 'log', data: { level: 'info', node: 'router', step_id: 'understanding', message: '已识别为销售额统计', payload: { phase: 'understanding' }, ts: 1 } },
    { event: 'log', data: { level: 'info', node: 'sql_validator', step_id: 'sql_validation', message: 'SQL 校验通过', payload: { phase: 'sql_validation' }, ts: 2 } },
    { event: 'log', data: { level: 'info', node: 'sql_executor', step_id: 'tool_result', message: '查询完成', payload: { phase: 'tool_result', tool: 'sql.query' }, ts: 3 } },
  ])

  expect(container.textContent).toContain('需求理解')
  expect(container.textContent).toContain('SQL 校验')
  expect(container.textContent).toContain('sql.query')
  act(() => root.unmount())
})
