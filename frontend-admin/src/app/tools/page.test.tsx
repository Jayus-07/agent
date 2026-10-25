import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getToolInventory = vi.hoisted(() => vi.fn())
const getToolStats = vi.hoisted(() => vi.fn())
const getToolContractChanges = vi.hoisted(() => vi.fn())
const getToolErrors = vi.hoisted(() => vi.fn())
const runToolFailureProbe = vi.hoisted(() => vi.fn())
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { push: vi.fn(), replace: vi.fn() },
}))

vi.mock('@/api/governance', () => ({
  getToolInventory,
  getToolStats,
  getToolContractChanges,
  getToolErrors,
  runToolFailureProbe,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => navigation.router,
  useSearchParams: () => navigation.params,
}))

import ToolsPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

beforeEach(() => {
  getToolInventory.mockResolvedValue({
    count: 2,
    lock_git_sha: 'abc1234',
    lock_error: '',
    tools: [{
      name: 'map.lookup',
      display_name: '地图检索',
      data_source: {
        type: 'mcp',
        provider: '高德',
        upstream_tool: 'amap.search',
        switch_env: 'AMAP_ENABLED',
        quota: { period: 'day', limit: 100, limit_env: 'AMAP_DAILY_LIMIT' },
      },
      quota_runtime: { status: 'ok', usage: 2, budget: 100 },
      args_schema: {
        city: { schema: { type: 'string' }, required: true, default: '__unset__' },
      },
      module: 'backend/tools/map/lookup.py',
      capabilities: ['map.lookup'],
      output_types: {},
      content_hash: 'abcdef1234567890',
      description_hash: 'description-hash',
      runtime: { calls: 4, success: 3, failures: 1, success_rate: 0.75, error_classes: { timeout: 1 } },
    }, {
      name: 'calculate_tool',
      display_name: '数学计算',
      data_source: null,
      quota_runtime: null,
      args_schema: {},
      module: 'backend/tools/calculator.py',
      capabilities: [],
      output_types: {},
      content_hash: '1122334455667788',
      description_hash: 'description-hash-2',
      runtime: { calls: 0, success: 0, failures: 0, success_rate: null, error_classes: {} },
    }],
  })
  getToolStats.mockResolvedValue({
    scope: 'production',
    totals: { tools_seen: 1, calls: 4, success: 3, failures: 1, success_rate: 0.75 },
    top_failed_tools: [{ tool: 'map.lookup', failures: 1, calls: 4, error_classes: { timeout: 1 } }],
    top_error_classes: [{ error_class: 'timeout', count: 1 }],
    skill_failures: [],
    tools: [],
  })
  getToolContractChanges.mockResolvedValue({ changes: [] })
  getToolErrors.mockResolvedValue({
    count: 1,
    errors: [{
      trace_id: 'trace-1',
      ts: '2026-10-02T10:20:30Z',
      session_id: 'session-1',
      question: '查询上海门店',
      tool: 'map.lookup',
      capability: 'map.lookup',
      skill: 'map_lookup_skill',
      error_code: 'timeout',
      error: '上游响应超时',
      latency_ms: 1234,
    }],
  })
  runToolFailureProbe.mockResolvedValue({
    trace_id: 'probe-trace-1', tool: 'map.lookup', error_class: 'timeout',
    status: 'timeout', simulated: true,
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
  navigation.params = new URLSearchParams()
  navigation.router.replace.mockClear()
})

describe('Tool 治理页面', () => {
  it('展开 Tool 详情时展示来源、参数和额度信息', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<ToolsPage />)
      await Promise.resolve()
    })

    const row = container.querySelector<HTMLTableRowElement>('tbody tr')
    expect(row).toBeTruthy()
    await act(async () => {
      row!.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('数据源与额度')
    expect(container.textContent).toContain('高德')
    expect(container.textContent).toContain('契约参数')
    expect(container.textContent).toContain('city')
    expect(container.textContent).toContain('今日 2 / 100')
  })

  it('失败详情展示完整来源链路、错误分类、耗时和 Trace', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<ToolsPage />)
      await Promise.resolve()
    })
    const row = container.querySelector<HTMLTableRowElement>('tbody tr')
    expect(row).toBeTruthy()

    await act(async () => {
      row!.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('来源链路')
    expect(container.textContent).toContain('用户问题')
    expect(container.textContent).toContain('查询上海门店')
    expect(container.textContent).toContain('Skill')
    expect(container.textContent).toContain('map_lookup_skill')
    expect(container.textContent).toContain('Capability')
    expect(container.textContent).toContain('Tool')
    expect(container.textContent).toContain('map.lookup')
    expect(container.textContent).toContain('错误分类')
    expect(container.textContent).toContain('超时')
    expect(container.textContent).toContain('耗时 1234 ms')
    expect(container.querySelector('a[href="/observability/traces/trace-1"]')?.textContent).toContain('查看完整 Trace')
  })

  it('强制失败测试入口展示模拟结果并回链 Trace', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(<ToolsPage />)
      await Promise.resolve()
    })

    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('生成失败 Trace'))
    expect(button).toBeTruthy()
    await act(async () => {
      button!.click()
      await Promise.resolve()
    })

    expect(runToolFailureProbe).toHaveBeenCalledWith('map.lookup', 'timeout')
    expect(container.textContent).toContain('已生成模拟失败')
    expect(container.querySelector('a[href="/observability/traces/probe-trace-1"]')).toBeTruthy()
  })

  async function mountPage() {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })
    await act(async () => {
      root.render(<ToolsPage />)
      await Promise.resolve()
    })
    return container
  }

  function visibleToolNames(container: HTMLElement): string[] {
    return Array.from(container.querySelectorAll('tbody > tr'))
      .filter((tr) => tr.querySelector('.font-mono'))
      .map((tr) => tr.querySelector('.font-mono')?.textContent ?? '')
  }

  it('搜索词过滤清单并同步到 URL', async () => {
    const container = await mountPage()
    expect(visibleToolNames(container).length).toBe(2)

    const input = container.querySelector<HTMLInputElement>('input[aria-label="搜索工具"]')
    expect(input).toBeTruthy()
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    await act(async () => {
      nativeSetter!.call(input!, 'calculate')
      input!.dispatchEvent(new Event('input', { bubbles: true }))
      await Promise.resolve()
    })

    const names = visibleToolNames(container)
    expect(names).toEqual(['calculate_tool'])
    expect(container.textContent).toContain('1 / 2 个')
    expect(navigation.router.replace).toHaveBeenCalled()
    const url = navigation.router.replace.mock.calls.at(-1)?.[0] as string
    expect(url).toContain('q=calculate')
  })

  it('数据源筛选与仅看失败组合过滤', async () => {
    const container = await mountPage()

    const ds = container.querySelector<HTMLSelectElement>('select[aria-label="数据源筛选"]')
    expect(ds).toBeTruthy()
    await act(async () => {
      ds!.value = 'mcp'
      ds!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })
    expect(visibleToolNames(container)).toEqual(['map.lookup'])

    const failedOnly = container.querySelector<HTMLInputElement>('input[type="checkbox"]')
    expect(failedOnly).toBeTruthy()
    await act(async () => {
      failedOnly!.click()
      await Promise.resolve()
    })
    // mcp + 仅失败：map.lookup（失败 1）仍命中
    expect(visibleToolNames(container)).toEqual(['map.lookup'])

    // 切回全部数据源 + 仅失败：calculate_tool（0 失败）被过滤
    await act(async () => {
      ds!.value = 'all'
      ds!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })
    expect(visibleToolNames(container)).toEqual(['map.lookup'])
  })

  it('变更分类筛选走服务端参数', async () => {
    getToolContractChanges.mockResolvedValue({
      changes: [{
        id: 1, from_lock_hash: 'a', to_lock_hash: 'b', classification: 'BREAKING',
        changed_tools: [], tool_count: 40, git_sha: 'd177694', detected_by: 'gen',
        created_at: '2026-10-10T07:02:58',
      }],
    })
    const container = await mountPage()

    const cls = container.querySelector<HTMLSelectElement>('select[aria-label="变更分类筛选"]')
    expect(cls).toBeTruthy()
    await act(async () => {
      cls!.value = 'BREAKING'
      cls!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(getToolContractChanges).toHaveBeenLastCalledWith({ classification: 'BREAKING' })
    expect(container.textContent).toContain('BREAKING')
  })
})
