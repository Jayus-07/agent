import { afterEach, expect, it, vi } from 'vitest'
import { dataQueryService } from './dataQuery'

afterEach(() => {
  vi.restoreAllMocks()
})

it('按会话读取 SQL 查询 SSE，并保留需求理解和 Tool 阶段', async () => {
  const response = new Response([
    'event: meta\n',
    'data: {"node_labels":{"query_understanding":"需求理解"}}\n\n',
    'event: log\n',
    'data: {"node":"sql_executor","step_id":"tool_result","message":"查询完成","payload":{"phase":"tool_result","tool":"sql.query"},"ts":1}\n\n',
    'event: done\n',
    'data: {"elapsed":0.1,"sources":[],"result":{"status":"success"}}\n\n',
  ].join(''))
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)

  const events = []
  for await (const event of dataQueryService.streamQuery('统计销售额', 'session-1')) {
    events.push(event)
  }

  expect(fetchMock).toHaveBeenCalledWith('/api/sql/query/stream', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ question: '统计销售额', session_id: 'session-1', reset_context: false }),
  }))
  expect(events.map(event => event.event)).toEqual(['meta', 'log', 'done'])
})
