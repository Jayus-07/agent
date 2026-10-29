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
const suggestMcpSemantic = vi.hoisted(() => vi.fn())
const saveMcpSemanticDraft = vi.hoisted(() => vi.fn())
const approveMcpSemantic = vi.hoisted(() => vi.fn())
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
vi.mock('@/api/mcp', () => ({
  getMcpTools,
  getMcpToolDetail,
  reviewMcpTool,
  grantMcpTool,
  disableMcpTool,
  testInvokeMcpTool,
  suggestMcpSemantic,
  saveMcpSemanticDraft,
  approveMcpSemantic,
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
  suggestMcpSemantic.mockResolvedValue({
    tool_id: 7,
    draft: {
      summary: 'AI 生成的摘要', scenarios: [], exclusions: [],
      capability_hint: null, status: 'suggested',
      generated_at: '2026-10-20T10:00:00Z', generated_by: 'user:admin',
    },
  })
  saveMcpSemanticDraft.mockResolvedValue({
    summary: '人工保存的摘要', scenarios: [], exclusions: [],
    capability_hint: null, status: 'edited',
    generated_at: '2026-10-20T10:00:00Z', generated_by: 'user:admin',
    edited_at: '2026-10-21T10:00:00Z', edited_by: 'user:admin',
  })
  approveMcpSemantic.mockResolvedValue({
    tool_id: 7, approved: true, description: '人工保存的摘要',
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

    // AI 语义 Tab：真实语义契约工作区（壳位已移除；无草稿 → 空态 + 生成按钮）
    expect(container.querySelector('[data-stub="demo"]')).toBeNull()
    const aiTab = Array.from(container.querySelectorAll('button[role="tab"]'))
      .find((item): item is HTMLButtonElement => item.textContent === 'AI 语义')
    expect(aiTab).toBeTruthy()
    expect(aiTab!.getAttribute('aria-disabled')).toBeNull()
    await act(async () => {
      aiTab!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('绝不包含凭据与地址')
    const suggestButton = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('生成语义建议'))
    expect(suggestButton).toBeTruthy()
    await act(async () => {
      suggestButton!.click()
      await Promise.resolve()
    })
    expect(suggestMcpSemantic).toHaveBeenCalledWith(7)

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

  it('AI 语义：草稿表单可编辑，保存草稿与审核并入调用对应 API', async () => {
    // happy-dom 未内置 confirm，直接替换为可断言的桩
    const confirmStub = vi.fn(() => true)
    window.confirm = confirmStub as unknown as typeof window.confirm
    // 打开抽屉前换成带草稿的工具详情
    getMcpToolDetail.mockResolvedValue({
      ...mcpTool,
      semantic_draft: {
        summary: 'AI 建议摘要', scenarios: ['场景一'], exclusions: ['写操作'],
        capability_hint: 'order.lookup', status: 'suggested',
        generated_at: '2026-10-20T10:00:00Z', generated_by: 'user:admin',
      },
    })
    const container = await mountPage()
    const mcpRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((tr) => tr.textContent?.includes('zhihu.get_answers'))
    const detailButton = Array.from(mcpRow!.querySelectorAll('button'))
      .find((item) => item.textContent?.trim() === '详情')
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })
    const aiTab = Array.from(container.querySelectorAll('button[role="tab"]'))
      .find((item): item is HTMLButtonElement => item.textContent === 'AI 语义')
    await act(async () => {
      aiTab!.click()
      await Promise.resolve()
    })

    // 待审状态 chip + 草稿表单预填
    expect(container.textContent).toContain('AI 生成（待审）')
    const textareas = Array.from(container.querySelectorAll('textarea'))
    expect(textareas[0]!.value).toBe('AI 建议摘要')

    const saveButton = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('保存草稿'))
    await act(async () => {
      saveButton!.click()
      await Promise.resolve()
    })
    expect(saveMcpSemanticDraft).toHaveBeenCalledWith(7, {
      summary: 'AI 建议摘要',
      scenarios: ['场景一'],
      exclusions: ['写操作'],
      capability_hint: 'order.lookup',
    })

    const approveButton = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('审核通过并入描述'))
    expect(approveButton).toBeTruthy()
    await act(async () => {
      approveButton!.click()
      await Promise.resolve()
    })
    expect(confirmStub).toHaveBeenCalled()
    expect(approveMcpSemantic).toHaveBeenCalledWith(7)
  })

  it('AI 语义：已并入后只读展示留痕并允许再次生成', async () => {
    getMcpToolDetail.mockResolvedValue({
      ...mcpTool,
      semantic_draft: {
        summary: '已并入的摘要', scenarios: [], exclusions: [],
        capability_hint: null, status: 'applied',
        generated_at: '2026-10-20T10:00:00Z', generated_by: 'user:admin',
      },
      semantic_approved: {
        summary: '已并入的摘要', scenarios: ['适用A'], exclusions: ['禁用B'],
        capability_hint: null, approved_at: '2026-10-21T11:00:00Z',
        approved_by: 'user:admin', description_after: '已并入的摘要',
      },
    })
    const container = await mountPage()
    const mcpRow = Array.from(container.querySelectorAll('tbody tr'))
      .find((tr) => tr.textContent?.includes('zhihu.get_answers'))
    const detailButton = Array.from(mcpRow!.querySelectorAll('button'))
      .find((item) => item.textContent?.trim() === '详情')
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })
    const aiTab = Array.from(container.querySelectorAll('button[role="tab"]'))
      .find((item): item is HTMLButtonElement => item.textContent === 'AI 语义')
    await act(async () => {
      aiTab!.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('已并入正式描述')
    expect(container.textContent).toContain('已并入的摘要')
    expect(container.textContent).toContain('适用：适用A')
    expect(container.textContent).toContain('禁用：禁用B')
    // 已并入后不再出现「保存草稿 / 审核通过并入描述」，只允许再次生成
    expect(Array.from(container.querySelectorAll('button'))
      .some((item) => item.textContent?.includes('保存草稿'))).toBe(false)
    expect(Array.from(container.querySelectorAll('button'))
      .some((item) => item.textContent?.includes('审核通过并入描述'))).toBe(false)
    const regen = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.includes('重新生成'))
    expect(regen).toBeTruthy()
    await act(async () => {
      regen!.click()
      await Promise.resolve()
    })
    expect(suggestMcpSemantic).toHaveBeenCalledWith(7)
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
