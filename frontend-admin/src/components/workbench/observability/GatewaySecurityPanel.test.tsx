import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getGatewayAuthMetrics: vi.fn(),
  getGatewayAccessLogs: vi.fn(),
}))

vi.mock('@/api/observability', () => ({
  getGatewayAuthMetrics: mocks.getGatewayAuthMetrics,
  getGatewayAccessLogs: mocks.getGatewayAccessLogs,
}))

import GatewaySecurityPanel from './GatewaySecurityPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.getGatewayAuthMetrics.mockReset()
  mocks.getGatewayAccessLogs.mockReset()
})

function metrics(available: boolean, overrides: Record<string, unknown> = {}) {
  return {
    available,
    window_hours: 24,
    ...(available
      ? {
          total_denied: 37,
          denied_by_reason: [{ reason: 'signature', count: 21 }, { reason: 'no-credential', count: 9 }],
          would_deny_by_reason: [{ reason: 'missing-user-id', count: 3 }],
          status_codes: [{ code: '401', count: 58 }, { code: '429', count: 214 }, { code: '200', count: 48000 }],
          denied_series: [{ ts: 1, value: 4 }, { ts: 2, value: 9 }],
          ...overrides,
        }
      : { error: 'prometheus unreachable', ...overrides }),
  }
}

function logs(available: boolean, rows: Array<Record<string, unknown>> = []) {
  return { available, window_hours: 24, total: rows.length, logs: rows }
}

function makeRow(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    ts: '2026-10-30T02:21:18.000Z',
    client_ip: '203.0.113.7',
    user_id: 'user:279',
    username: 'operator',
    auth_type: 'jwt',
    trace_id: '',
    method: 'POST',
    uri: '/api/rag/ask',
    query: '',
    status: 401,
    bytes: 120,
    duration_ms: 3,
    ua: '',
    ...overrides,
  }
}

async function waitText(container: HTMLDivElement, text: string): Promise<void> {
  // React Query 解析是异步的：轮询等待目标文本出现（上限 ~1s）
  for (let i = 0; i < 20; i++) {
    if (container.textContent?.includes(text)) return
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)) })
  }
}

async function mountPanel(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <GatewaySecurityPanel />
      </QueryClientProvider>,
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('GatewaySecurityPanel（网关安全重构版）', () => {
  it('数据源不可用时两区块显式降级，不伪造空统计', async () => {
    mocks.getGatewayAuthMetrics.mockResolvedValue(metrics(false))
    mocks.getGatewayAccessLogs.mockResolvedValue(logs(false))
    const container = await mountPanel()
    await waitText(container, '网关指标数据源不可用')

    expect(container.textContent).toContain('网关指标数据源不可用')
    expect(container.textContent).toContain('访问日志数据源不可用')
    expect(container.textContent).toContain('alembic upgrade head')
  })

  it('正常数据渲染四卡/原因分布/would_deny 观察与审计明细表', async () => {
    mocks.getGatewayAuthMetrics.mockResolvedValue(metrics(true))
    mocks.getGatewayAccessLogs.mockResolvedValue(logs(true, [
      makeRow({ status: 401, auth_type: 'api-key', user_id: '' }),
      makeRow({ status: 200, auth_type: '', user_id: 'sys:healthcheck', uri: '/health' }),
    ]))
    const container = await mountPanel()
    await waitText(container, '签名校验失败')

    expect(container.textContent).toContain('认证拒绝')
    expect(container.textContent).toContain('签名校验失败')
    expect(container.textContent).toContain('未拦截观察 would_deny')
    // 审计明细：默认仅异常视角（abnormal_only=true），401 行可见
    expect(mocks.getGatewayAccessLogs).toHaveBeenCalledWith(expect.objectContaining({ abnormalOnly: true, includeNoise: false, hours: 24 }))
    expect(container.textContent).toContain('203.0.113.7')
    expect(container.textContent).toContain('/api/rag/ask')
  })
})
