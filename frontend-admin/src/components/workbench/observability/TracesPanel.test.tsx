import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceRecord } from '@/types/trace'

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
  default: ({ t }: { t: TraceRecord }) => <tr data-trace-id={t.id}><td>{t.id}</td></tr>,
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

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.listTraces.mockReset().mockResolvedValue([])
  Object.values(mocks.toast).forEach((fn) => fn.mockClear())
  mocks.navigation.params = new URLSearchParams()
  mocks.navigation.router.push.mockClear()
  mocks.navigation.router.replace.mockClear()
  vi.restoreAllMocks()
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

function makeTrace(status: TraceRecord['status'], id: string): TraceRecord {
  return {
    id,
    request_id: `request-${id}`,
    timestamp: new Date().toISOString(),
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 100,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    status,
    workflow_name: 'workflow',
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

async function setSearchParams(params: URLSearchParams) {
  mocks.navigation.params = params
  await act(async () => {
    mounted?.root.render(<TracesPanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
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

  it('保留旧 URL 的运行中筛选，但明确标记 BLOCKED 而不报告空结果', async () => {
    mocks.navigation.params = new URLSearchParams('tab=traces&status=running')
    const container = await mountPanel()

    expect(container.textContent).toContain('运行中记录暂不可安全查询')
    expect(container.textContent).toContain('BLOCKED')
    expect(container.textContent).toContain('当前筛选 暂不可统计')
    expect(container.textContent).not.toContain('当前筛选条件下无匹配记录')
    expect(container.textContent).not.toContain('当前授权范围内没有运行记录')

    const clearButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('清除运行中筛选'))
    expect(clearButton).toBeTruthy()
    await act(async () => clearButton?.click())
    expect(mocks.navigation.router.replace).toHaveBeenCalledWith(
      expect.stringContaining('status=all'),
      { scroll: false },
    )
  })

  it('默认列表与 CSV 导出排除历史 running 并解释其状态未确认', async () => {
    mocks.listTraces.mockResolvedValue([
      makeTrace('running', 'historical-running'),
      makeTrace('success', 'confirmed-success'),
    ])
    const container = await mountPanel()

    expect(container.querySelector('[data-trace-id="historical-running"]')).toBeNull()
    expect(container.querySelector('[data-trace-id="confirmed-success"]')).toBeTruthy()
    expect(container.textContent).toContain('已从列表与导出中排除')

    const createObjectURL = vi.fn((_blob: Blob) => 'blob:test')
    const originalCreateObjectURL = URL.createObjectURL
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const exportButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('导出安全字段'))
    await act(async () => exportButton?.click())
    const exported = await (createObjectURL.mock.calls[0][0] as Blob).text()

    expect(exported).toContain('confirmed-success')
    expect(exported).not.toContain('historical-running')
    if (originalCreateObjectURL) Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: originalCreateObjectURL })
    else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL
  })

  it('Tool 筛选快速切换时忽略较早请求的迟到响应', async () => {
    const toolA = deferred<TraceRecord[]>()
    const toolB = deferred<TraceRecord[]>()
    mocks.listTraces
      .mockResolvedValueOnce([])
      .mockImplementation((_limit, _status, _source, toolId) => {
        if (toolId === 'tool-a') return toolA.promise
        if (toolId === 'tool-b') return toolB.promise
        return Promise.resolve([])
      })
    const container = await mountPanel()

    await setSearchParams(new URLSearchParams('tab=traces&has_tool=tool-a'))
    await setSearchParams(new URLSearchParams('tab=traces&has_tool=tool-b'))
    expect(mocks.listTraces.mock.calls.some((call) => call[3] === 'tool-a')).toBe(true)
    expect(mocks.listTraces.mock.calls.some((call) => call[3] === 'tool-b')).toBe(true)

    await act(async () => { toolB.resolve([makeTrace('success', 'tool-b-result')]); await toolB.promise })
    await act(async () => { toolA.resolve([makeTrace('success', 'tool-a-stale-result')]); await toolA.promise })

    expect(container.querySelector('[data-trace-id="tool-b-result"]')).toBeTruthy()
    expect(container.querySelector('[data-trace-id="tool-a-stale-result"]')).toBeNull()
  })
})
