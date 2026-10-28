import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getToolInventory = vi.hoisted(() => vi.fn())
const getToolStats = vi.hoisted(() => vi.fn())
const getToolContractChanges = vi.hoisted(() => vi.fn())
const getToolErrors = vi.hoisted(() => vi.fn())
const runToolFailureProbe = vi.hoisted(() => vi.fn())
const getMcpTools = vi.hoisted(() => vi.fn())
const getMcpToolDetail = vi.hoisted(() => vi.fn())
const reviewMcpTool = vi.hoisted(() => vi.fn())
const grantMcpTool = vi.hoisted(() => vi.fn())
const disableMcpTool = vi.hoisted(() => vi.fn())
const testInvokeMcpTool = vi.hoisted(() => vi.fn())
const runFailureAnalysis = vi.hoisted(() => vi.fn())
const getFailureAnalyses = vi.hoisted(() => vi.fn())
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
vi.mock('@/api/failureAnalysis', () => ({
  runFailureAnalysis,
  getFailureAnalyses,
}))
vi.mock('@/api/mcp', () => ({
  getMcpTools,
  getMcpToolDetail,
  reviewMcpTool,
  grantMcpTool,
  disableMcpTool,
  testInvokeMcpTool,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => navigation.router,
  usePathname: () => '/tools',
  useSearchParams: () => navigation.params,
}))

import ToolsPage from './page'
import type { McpTool } from '@/api/mcp'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

/** 动态 MCP 工具 fixture（registry 统一目录的动态行 + 详情抽屉数据源） */
const mcpTool: McpTool = {
  id: 7,
  connection_id: 'conn-1',
  connection_name: 'zhihu-open-api',
  conn_transport: 'streamable_http',
  tool_name: 'get_answers',
  capability: 'zhihu.get_answers',
  capability_slug: 'zhihu-get-answers',
  description: '获取知乎回答列表',
  status: 'approved',
  content_hash: 'aabbccdd11223344',
  approved_hash: 'aabbccdd11223344',
  schema_changed: false,
  governance: { operation: 'read', risk_level: 'R1', domain: 'data' },
  examples: [],
  review_notes: '',
  reviewed_by: 'admin',
  reviewed_at: '2026-10-01T10:00:00Z',
  last_seen_version: 2,
  retired_reason: '',
  updated_at: null,
  grants: [],
  snapshots: [{
    discovery_version: 2,
    content_hash: 'aabbccdd11223344',
    input_schema: {
      type: 'object',
      properties: { question_id: { type: 'string', description: '问题 ID' } },
    },
    output_schema: null,
    description: '获取回答',
    discovered_at: '2026-10-01T09:00:00Z',
    invocation: null,
  }],
}

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
  getMcpTools.mockResolvedValue([mcpTool])
  getMcpToolDetail.mockResolvedValue(mcpTool)
  reviewMcpTool.mockResolvedValue({ tool_id: 7, status: 'approved' })
  grantMcpTool.mockResolvedValue({ tool_id: 7, product_key: 'agent', granted: true })
  disableMcpTool.mockResolvedValue({ tool_id: 7, status: 'disabled' })
  testInvokeMcpTool.mockResolvedValue({
    tool_id: 7, capability: 'zhihu.get_answers', product_key: 'internal',
    tool_status: 'success', error_code: '', error_message: '', elapsed_ms: 42, result: { ok: true },
  })
  runFailureAnalysis.mockResolvedValue({
    analysis_id: 'tfa_abc123def456',
    clusters: [{
      tool: 'map.lookup',
      error_class: 'timeout',
      attribution: '上游超时预算不足',
      suggestions: ['提高超时预算', '增加降级路径'],
      candidate_eval_cases: [{
        question: '查询上海门店的营业时间',
        expected_capability: 'map.lookup',
        expectation_note: '应在超时预算内返回',
      }],
    }],
    created_at: '2026-10-28T12:00:00+00:00',
  })
  getFailureAnalyses.mockResolvedValue({ count: 0, analyses: [] })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
  navigation.params = new URLSearchParams()
  navigation.router.replace.mockClear()
  navigation.router.push.mockClear()
})

async function mountPage(initialQuery = '') {
  navigation.params = new URLSearchParams(initialQuery)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<ToolsPage />)
    await Promise.resolve()
  })
  mounted.push({ container, root })
  return container
}

function clickTab(container: HTMLElement, label: string) {
  const tab = Array.from(container.querySelectorAll<HTMLButtonElement>('button[role="tab"]'))
    .find((item) => item.textContent?.includes(label))
  expect(tab, `找不到页级 Tab：${label}`).toBeTruthy()
  act(() => { tab!.click() })
  expect(navigation.router.push).toHaveBeenCalled()
  const url = navigation.router.push.mock.calls.at(-1)?.[0] as string
  expect(url).toContain('tab=')
}

/** 目录/清单可见行名（取每行第一个 .font-mono 文本） */
function visibleToolNames(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('tbody > tr'))
    .map((tr) => tr.querySelector('.font-mono')?.textContent ?? '')
    .filter((text) => text.length > 0)
}

describe('Tool 治理页面（V2 三 subTab）', () => {
  it('registry 统一目录：静态行展开只读详情，动态行展示来源/业务域/状态徽章', async () => {
    const container = await mountPage()

    // 动态行：来源徽章 + 连接名 + 业务域 + 状态 chip
    expect(container.textContent).toContain('zhihu.get_answers')
    expect(container.textContent).toContain('MCP·zhihu-open-api')
    expect(container.textContent).toContain('data')
    expect(container.textContent).toContain('已生效')

    // 静态行展开（现有只读展示迁移动作保留）
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

  it('registry 指标条覆盖工具总数/运行时可用/待审核/契约漂移', async () => {
    const container = await mountPage()
    expect(container.textContent).toContain('工具总数')
    expect(container.textContent).toContain('运行时可用')
    expect(container.textContent).toContain('待审核')
    expect(container.textContent).toContain('契约漂移')
  })

  it('失败详情展示完整来源链路、错误分类、耗时和 Trace', async () => {
    const container = await mountPage()
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

  it('动态工具详情抽屉：概览状态、执行策略含业务域与授权、AI 语义为置灰 Tab', async () => {
    const container = await mountPage()

    // 动态行「详情」打开抽屉（定位动态行内的按钮，避免命中静态行展开按钮）
    const mcpRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((tr) => tr.textContent?.includes('zhihu.get_answers'))
    expect(mcpRow).toBeTruthy()
    const detailButton = Array.from(mcpRow!.querySelectorAll('button'))
      .find((item) => item.textContent?.trim() === '详情')
    expect(detailButton).toBeTruthy()
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })

    // 概览：状态三态 + 连接 + 描述
    expect(container.textContent).toContain('已生效')
    expect(container.textContent).toContain('zhihu-open-api')
    expect(container.textContent).toContain('获取知乎回答列表')

    // AI 语义 Tab：置灰壳位（不可点击 + data-stub 标记 + 常驻提示）
    const stubTab = container.querySelector('[data-stub="demo"]')
    expect(stubTab?.textContent).toContain('AI 语义')
    expect(stubTab?.getAttribute('aria-disabled')).toBe('true')
    expect(container.textContent).toContain('AI 语义契约建议属下一批')

    // 执行策略：审核表单 + 业务域 select + 授权开关
    const policyTab = Array.from(container.querySelectorAll('button[role="tab"]'))
      .find((item): item is HTMLButtonElement => item.textContent === '执行策略')
    await act(async () => {
      policyTab!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('业务域')
    expect(container.textContent).toContain('审核通过并生效')
    const agentGrant = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('主助手'))
    expect(agentGrant).toBeTruthy()
    await act(async () => {
      agentGrant!.click()
      await Promise.resolve()
    })
    expect(grantMcpTool).toHaveBeenCalledWith(7, 'agent', false)

    // 调试 Tab：只读测试调用迁入
    const debugTab = Array.from(container.querySelectorAll('button[role="tab"]'))
      .find((item): item is HTMLButtonElement => item.textContent === '调试')
    await act(async () => {
      debugTab!.click()
      await Promise.resolve()
    })
    const invokeButton = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('执行测试调用'))
    expect(invokeButton).toBeTruthy()
    await act(async () => {
      invokeButton!.click()
      await Promise.resolve()
    })
    expect(testInvokeMcpTool).toHaveBeenCalled()
    expect(container.textContent).toContain('success')
  })

  it('强制失败测试入口在「运行质量与失败」subTab，展示模拟结果并回链 Trace', async () => {
    const container = await mountPage('tab=quality')

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

  it('quality subTab 重组统计卡、失败 Tool 与七分类', async () => {
    const container = await mountPage('tab=quality')
    expect(container.textContent).toContain('调用总数')
    expect(container.textContent).toContain('成功率')
    expect(container.textContent).toContain('失败次数')
    expect(container.textContent).toContain('失败 Tool')
    expect(container.textContent).toContain('失败原因分布')
    expect(container.textContent).toContain('超时')
  })

  it('quality subTab AI 失败分析：生成调用、聚类卡渲染与「仅建议」边界标注', async () => {
    const container = await mountPage('tab=quality')

    // 区块常驻：说明文案含数据边界（不含用户参数原文与凭据）
    expect(container.textContent).toContain('AI 失败分析')
    expect(container.textContent).toContain('不含用户参数原文与凭据')
    expect(container.textContent).toContain('最近分析')

    // 生成分析：默认 60 分钟窗
    const windowSelect = container.querySelector<HTMLSelectElement>('select[aria-label="分析时间窗"]')
    expect(windowSelect).toBeTruthy()
    const button = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('生成分析'))
    expect(button).toBeTruthy()
    await act(async () => {
      button!.click()
      await Promise.resolve()
    })

    expect(runFailureAnalysis).toHaveBeenCalledWith({ window_minutes: 60 })
    // 聚类卡：工具/错误分类/归因/修复建议/候选用例
    expect(container.textContent).toContain('map.lookup')
    expect(container.textContent).toContain('上游超时预算不足')
    expect(container.textContent).toContain('提高超时预算')
    expect(container.textContent).toContain('查询上海门店的营业时间')
    // 候选用例仅建议的边界标注常驻
    expect(container.textContent).toContain('应用需走评测中心审核与回归')
    // 生成后刷新最近分析列表
    expect(getFailureAnalyses).toHaveBeenCalled()
  })

  it('quality subTab AI 失败分析：空聚类展示无失败提示，可回看最近分析', async () => {
    runFailureAnalysis.mockResolvedValue({
      analysis_id: 'tfa_empty000001',
      clusters: [],
      created_at: '2026-10-28T13:00:00+00:00',
    })
    getFailureAnalyses.mockResolvedValue({
      count: 1,
      analyses: [{
        id: 'tfa_hist0000001',
        window_minutes: 360,
        input_summary: {},
        result: { clusters: [] },
        model: 'doubao-seed-2.0-mini',
        created_by: 'user:u-admin',
        created_at: '2026-10-28T11:00:00+00:00',
        cluster_count: 0,
      }],
    })
    const container = await mountPage('tab=quality')

    const button = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('生成分析'))
    await act(async () => {
      button!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('没有 Tool 失败记录')

    // 最近分析点击回看：结果面板切到历史分析 id
    const historyButton = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('窗口 360 分钟'))
    expect(historyButton).toBeTruthy()
    await act(async () => {
      historyButton!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('分析 tfa_hist0000001')
  })

  it('registry 搜索词过滤合并目录并同步到 URL', async () => {
    const container = await mountPage()
    // 静态 2 + 动态 1
    expect(container.textContent).toContain('3 / 3 个')

    const input = container.querySelector<HTMLInputElement>('input[aria-label="搜索工具"]')
    expect(input).toBeTruthy()
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    await act(async () => {
      nativeSetter!.call(input!, 'calculate')
      input!.dispatchEvent(new Event('input', { bubbles: true }))
      await Promise.resolve()
    })

    const names = visibleToolNames(container)
    expect(names).toHaveLength(1)
    expect(names[0]).toContain('calculate_tool')
    expect(container.textContent).toContain('1 / 3 个')
    expect(navigation.router.replace).toHaveBeenCalled()
    const url = navigation.router.replace.mock.calls.at(-1)?.[0] as string
    expect(url).toContain('q=calculate')
  })

  it('页级 Tab 切换同步 URL query', async () => {
    const container = await mountPage()
    clickTab(container, '契约与变更历史')
  })

  it('contract subTab：数据源筛选与仅看失败组合过滤', async () => {
    const container = await mountPage('tab=contract')

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

  it('contract subTab：变更分类筛选走服务端参数', async () => {
    getToolContractChanges.mockResolvedValue({
      changes: [{
        id: 1, from_lock_hash: 'a', to_lock_hash: 'b', classification: 'BREAKING',
        changed_tools: [], tool_count: 40, git_sha: 'd177694', detected_by: 'gen',
        created_at: '2026-10-10T07:02:58',
      }],
    })
    const container = await mountPage('tab=contract')

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
