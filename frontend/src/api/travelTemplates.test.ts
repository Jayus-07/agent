import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyTravelTemplate, fetchTravelTemplate, fetchTravelTemplates } from './travel'

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('平台旅行模板 API', () => {
  afterEach(() => vi.restoreAllMocks())

  it('查询模板目录与指定模板详情', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ templates: [{ slug: 'hangzhou-slow' }] }))
      .mockResolvedValueOnce(response({ template: { slug: 'hangzhou/slow' } }))

    await expect(fetchTravelTemplates()).resolves.toEqual([{ slug: 'hangzhou-slow' }])
    await expect(fetchTravelTemplate('hangzhou/slow')).resolves.toMatchObject({ slug: 'hangzhou/slow' })
    expect(String(fetchSpy.mock.calls[1][0])).toContain('/api/travel/templates/hangzhou%2Fslow')
  })

  it('模板副本只提交模板标识和会话标识', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ conversation_id: 'trip-12345678', plan_version: 1, plan_status: 'confirmed' }, 201),
    )

    await copyTravelTemplate('hangzhou-slow', 'trip-12345678')

    expect(String(fetchSpy.mock.calls[0][0])).toBe('/api/travel/templates/hangzhou-slow/copy')
    expect(fetchSpy.mock.calls[0][1]?.method).toBe('POST')
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({ conversation_id: 'trip-12345678' })
  })

  it('副本持久化失败时将错误交给调用方', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ detail: '行程暂未保存' }, 503))

    await expect(copyTravelTemplate('hangzhou-slow', 'trip-12345678')).rejects.toThrow()
  })
})
