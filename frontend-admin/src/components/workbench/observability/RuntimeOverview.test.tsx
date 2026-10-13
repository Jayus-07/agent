import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

const mocks = vi.hoisted(() => ({
  listTraces: vi.fn(),
  navigation: {
    pathname: '/observability/monitoring',
    params: new URLSearchParams('tab=overview'),
    router: { push: vi.fn(), replace: vi.fn() },
  },
}))

vi.mock('@/api/observability', () => ({ listTraces: mocks.listTraces }))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => <a href={href} {...props}>{children}</a>,
}))
vi.mock('next/navigation', () => ({
  usePathname: () => mocks.navigation.pathname,
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))

import RuntimeOverview from './RuntimeOverview'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  mocks.listTraces.mockReset()
  mocks.navigation.params = new URLSearchParams('tab=overview')
})

function makeTrace(): TraceRecord {
  return {
    id: 'active-trace',
    request_id: 'request-1',
    timestamp: new Date().toISOString(),
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 0,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    status: 'running',
  }
}

async function mountOverview(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<RuntimeOverview />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('RuntimeOverview', () => {
  it('不从历史 Trace 样本推断活跃运行数量，也不生成运行中筛选链接', async () => {
    mocks.listTraces.mockResolvedValue([makeTrace()])
    const container = await mountOverview()

    expect(mocks.listTraces).toHaveBeenCalledWith(200)
    expect(container.textContent).toContain('运行中')
    expect(container.textContent).toContain('暂不可统计')
    expect(container.textContent).toContain('BLOCKED')
    expect(container.querySelector('a[href*="status=running"]')).toBeNull()
  })
})
