import { beforeEach, describe, expect, it, vi } from 'vitest'

const authMock = vi.hoisted(() => ({
  bearerHeaders: vi.fn(() => ({ Authorization: 'Bearer test-token' })),
  handleAuthFailure: vi.fn(),
  tryRefreshOnce: vi.fn(async () => false),
}))

vi.mock('@/lib/auth', () => authMock)

import { requestHandoff } from './cs'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('requestHandoff', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('使用直接 API 和 Idempotency-Key header，不发送自然语言 body', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        handoff_id: 'handoff-1',
        conversation_id: 'conversation-1',
        handoff_state: 'waiting_human',
        total_deadline_at: '2026-09-20T12:00:00+00:00',
        reused: false,
      }),
    )

    await expect(requestHandoff('conversation/1', 'key-1')).resolves.toMatchObject({
      handoff_id: 'handoff-1',
      reused: false,
    })

    const [, init] = fetchSpy.mock.calls[0]
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/cs/conversations/conversation%2F1/handoff')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeUndefined()
    expect(init?.headers).toMatchObject({
      Authorization: 'Bearer test-token',
      'Idempotency-Key': 'key-1',
    })
  })
})
