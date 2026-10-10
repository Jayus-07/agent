import { afterEach, describe, expect, it, vi } from 'vitest'
import { listSessionsPage, SESSION_PAGE_SIZE } from './memory'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('记忆会话分页 API', () => {
  afterEach(() => vi.restoreAllMocks())

  it('读取首屏并传递分页游标', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      sessions: [{ session_id: 'session-1', title: '杭州计划', message_count: 3 }],
      total: 1,
      has_more: false,
    }))

    await expect(listSessionsPage(7, 'cursor /?', 'session/1')).resolves.toMatchObject({
      total: 1,
      has_more: false,
      sessions: [{ session_id: 'session-1' }],
    })
    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      '/api/memory/sessions?limit=7&before=cursor+%2F%3F&before_session_id=session%2F1',
    )
  })

  it('默认使用统一的首屏数量', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({
      sessions: [], total: 0, has_more: false,
    }))

    await listSessionsPage()

    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      `/api/memory/sessions?limit=${SESSION_PAGE_SIZE}`,
    )
  })
})