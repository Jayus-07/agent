import { beforeEach, describe, expect, it, vi } from 'vitest'

const authMock = vi.hoisted(() => ({
  bearerHeaders: vi.fn(() => ({ Authorization: 'Bearer test-token' })),
  handleAuthFailure: vi.fn(),
  tryRefreshOnce: vi.fn(async () => false),
}))

vi.mock('@/lib/auth', () => authMock)

import { confirmAction, getMyPendingAction, requestHandoff } from './cs'

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
    // 缺陷5 归一后 headers 以 Headers 实例交给 fetch
    const sent = new Headers(init?.headers)
    expect(sent.get('Authorization')).toBe('Bearer test-token')
    expect(sent.get('Idempotency-Key')).toBe('key-1')
  })
})

describe('待确认操作 API', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('确认时提交服务端版本和客户端幂等键', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ status: 'success', answer: '已提交', confirmation_state: 'success' }),
    )
    const snapshot = {
      proposal_id: 'proposal-1',
      version: 4,
      action_type: 'refund',
      masked_target: '订单尾号 ****1234',
      summary: '申请退款',
      expires_at: null,
      state: 'pending' as const,
    }

    await confirmAction('session/1', snapshot, 'confirm', 'f20c8d07-bc8f-4f15-9f70-55c8d3e5a8b1')

    expect(fetchSpy.mock.calls[0][0]).toBe('/api/cs/confirm')
    const init = fetchSpy.mock.calls[0][1]
    expect(JSON.parse(String(init?.body))).toEqual({
      session_id: 'session/1',
      decision: 'confirm',
      proposal_id: 'proposal-1',
      expected_version: 4,
      client_action_id: 'f20c8d07-bc8f-4f15-9f70-55c8d3e5a8b1',
    })
  })

  it('按会话读取权威待确认快照', async () => {
    const snapshot = {
      proposal_id: 'proposal-1', version: 1, action_type: 'refund',
      masked_target: '订单', summary: '申请退款', expires_at: null, state: 'pending',
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ pending_action: snapshot }),
    )

    await expect(getMyPendingAction('session/1')).resolves.toEqual(snapshot)
    expect(fetchSpy.mock.calls[0][0]).toBe(
      '/api/cs/conversations/my/session%2F1/pending',
    )
  })
})
