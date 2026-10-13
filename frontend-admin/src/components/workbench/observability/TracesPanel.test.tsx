import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listTraces: vi.fn().mockResolvedValue([]),
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), show: vi.fn() },
  navigation: {
    pathname: '/observability/monitoring',
    params: new URLSearchParams(),
    router: { push: vi.fn(), replace: vi.fn() },
  },
}))

vi.mock('@/api/observability', () => ({ listTraces: mocks.listTraces }))
vi.mock('@/components/shared/Toast', () => ({ useToast: () => mocks.toast }))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => <a href={href} {...props}>{children}</a>,
}))
vi.mock('@/components/observability/trace/TraceBreadcrumb', () => ({ default: () => <div /> }))
vi.mock('@/components/observability/trace/TraceFilter', () => ({ default: () => <div /> }))
vi.mock('@/components/observability/trace/TraceRow', () => ({
  VALID_COL_KEYS: ['status', 'question', 'duration', 'usage', 'time', 'actions'],
  default: () => <tr />,
}))
vi.mock('next/navigation', () => ({
  usePathname: () => mocks.navigation.pathname,
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))

import TracesPanel from './TracesPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  mocks.listTraces.mockReset().mockResolvedValue([])
  Object.values(mocks.toast).forEach((fn) => fn.mockClear())
  mocks.navigation.params = new URLSearchParams()
  mocks.navigation.router.push.mockClear()
  mocks.navigation.router.replace.mockClear()
})

async function mountPanel(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<TracesPanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

describe('TracesPanel', () => {
  it('从 URL 读取 Tool 下钻条件并调用现有授权 Trace 查询', async () => {
    mocks.navigation.params = new URLSearchParams('tab=traces&has_tool=sql.query&range=6h&status=error')

    const container = await mountPanel()

    expect(mocks.listTraces).toHaveBeenCalledWith(200, undefined, undefined, 'sql.query')
    expect(container.textContent).toContain('仅显示匹配 Tool')
    expect(container.textContent).toContain('当前授权范围内没有运行记录')
  })

  it('将成功返回的空列表与请求失败分别呈现', async () => {
    const empty = await mountPanel()
    expect(empty.textContent).toContain('Trace API 成功返回 0 条')
    act(() => mounted?.root.unmount())
    mounted?.container.remove()
    mounted = null

    mocks.listTraces.mockRejectedValueOnce({ status: 403 })
    const failed = await mountPanel()
    expect(failed.textContent).toContain('执行记录暂不可用')
    expect(failed.textContent).toContain('当前账号无权读取')
  })
})
