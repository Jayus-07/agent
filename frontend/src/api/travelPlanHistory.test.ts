import { afterEach, describe, expect, it, vi } from 'vitest'
import { deleteTravelPlan } from './travel'

describe('个人行程记录删除 API', () => {
  afterEach(() => vi.restoreAllMocks())

  it('使用行程会话标识调用 DELETE', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ conversation_id: 'trip/1', deleted: true }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ))

    await expect(deleteTravelPlan('trip/1')).resolves.toEqual({
      conversation_id: 'trip/1', deleted: true,
    })
    expect(String(fetchSpy.mock.calls[0][0])).toBe('/api/travel/plans/trip%2F1')
    expect(fetchSpy.mock.calls[0][1]?.method).toBe('DELETE')
  })

  it('服务端删除失败时返回错误给页面', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ detail: '行程未能删除' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } },
    ))

    await expect(deleteTravelPlan('trip-1')).rejects.toThrow()
  })
})
