import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getMcpOverview = vi.hoisted(() => vi.fn())
const getMcpConnections = vi.hoisted(() => vi.fn())
const getMcpTools = vi.hoisted(() => vi.fn())
const exportMcpConfiguration = vi.hoisted(() => vi.fn())
const createMcpConnection = vi.hoisted(() => vi.fn())
const updateMcpConnection = vi.hoisted(() => vi.fn())
const deleteMcpConnection = vi.hoisted(() => vi.fn())
const testMcpConnection = vi.hoisted(() => vi.fn())
const discoverMcpTools = vi.hoisted(() => vi.fn())
const setMcpConnectionEnabled = vi.hoisted(() => vi.fn())
const registerHttpTool = vi.hoisted(() => vi.fn())
const updateHttpToolInvocation = vi.hoisted(() => vi.fn())
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  // push 语义对齐 useWorkbenchTab：更新 query，驱动页级 Tab 真实切换
  router: {
    push: vi.fn((url: string) => {
      navigation.params = new URLSearchParams(url.split('?')[1] ?? '')
    }),
    replace: vi.fn(),
  },
}))

vi.mock('@/api/mcp', () => ({
  getMcpOverview,
  getMcpConnections,
  getMcpTools,
  exportMcpConfiguration,
  createMcpConnection,
  updateMcpConnection,
  deleteMcpConnection,
  testMcpConnection,
  discoverMcpTools,
  setMcpConnectionEnabled,
  registerHttpTool,
  updateHttpToolInvocation,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => navigation.router,
  usePathname: () => '/mcp',
  useSearchParams: () => navigation.params,
}))

import McpPage from './page'
import type { McpConnection, McpTool } from '@/api/mcp'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

const connMcp: McpConnection = {
  id: 'conn-1',
  name: 'zhihu-open-api',
  base_url: 'https://zhihu.example/mcp',
  transport: 'streamable_http',
  auth_type: 'static_bearer',
  network_scope: 'public',
  enabled: true,
  status: 'connected',
  last_error: '',
  last_synced_at: '2026-10-20T08:00:00Z',
  discovery_version: 3,
  credential: { configured: true, fingerprint: 'fp-1', last4: '6789', version: 1 },
  tenant_id: 't1',
  created_by: 'admin',
  updated_by: 'admin',
  updated_at: '2026-10-19T08:00:00Z',
}

const connHttp: McpConnection = {
  ...connMcp,
  id: 'conn-2',
  name: 'orders-rest',
  base_url: 'https://api.example.com',
  transport: 'http_api',
  auth_type: 'none',
  enabled: true,
  status: 'error',
  last_error: '上游 503',
  last_synced_at: null,
  discovery_version: 1,
  credential: { configured: false, fingerprint: '', last4: '', version: 0 },
}

const mcpTool: McpTool = {
  id: 7,
  connection_id: 'conn-1',
  connection_name: 'zhihu-open-api',
  conn_transport: 'streamable_http',
  tool_name: 'get_answers',
  capability: 'zhihu.get_answers',
  capability_slug: 'zhihu-get-answers',
  description: '获取回答列表',
  status: 'approved',
  content_hash: 'aabbccdd11223344',
  approved_hash: 'aabbccdd11223344',
  schema_changed: false,
  governance: { operation: 'read', risk_level: 'R1' },
  examples: [],
  review_notes: '',
  reviewed_by: 'admin',
  reviewed_at: null,
  last_seen_version: 2,
  retired_reason: '',
  updated_at: null,
  grants: [],
  snapshots: [],
}

beforeEach(() => {
  getMcpOverview.mockResolvedValue({
    loaded: true,
    active_tools: ['zhihu.get_answers'],
    connection_count: 2,
    grant_count: 1,
    derive_errors: [],
    refreshed_at: Date.now(),
  })
  getMcpConnections.mockResolvedValue([connMcp, connHttp])
  getMcpTools.mockResolvedValue([mcpTool])
  exportMcpConfiguration.mockResolvedValue({ connections: [] })
  testMcpConnection.mockResolvedValue({ ok: true, elapsed_ms: 20, tool_count: 1 })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
  navigation.params = new URLSearchParams()
  navigation.router.push.mockClear()
})

async function mountPage(initialQuery = '') {
  navigation.params = new URLSearchParams(initialQuery)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<McpPage />)
    await Promise.resolve()
    await Promise.resolve()
  })
  mounted.push({ container, root })
  return container
}

function clickDrawerTab(container: HTMLElement, label: string) {
  const tab = Array.from(container.querySelectorAll<HTMLButtonElement>('button[role="tab"]'))
    .find((item) => item.textContent?.trim() === label)
  expect(tab, `找不到抽屉 Tab：${label}`).toBeTruthy()
  act(() => { tab!.click() })
}

describe('连接中心页面（V2：连接为一级资产）', () => {
  it('顶部指标条 + 页级 Tab + 动作区（配置导出/新建连接）', async () => {
    const container = await mountPage()

    expect(container.textContent).toContain('连接总数')
    expect(container.textContent).toContain('健康连接')
    expect(container.textContent).toContain('连接异常')
    expect(container.textContent).toContain('Schema 漂移')
    expect(container.textContent).toContain('发现工具数')

    expect(Array.from(container.querySelectorAll('button'))
      .some((b) => b.textContent?.includes('配置导出'))).toBe(true)
    const newButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('新建连接'))
    expect(newButton).toBeTruthy()
    await act(async () => {
      newButton!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('创建连接')

    const exportButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('配置导出'))
    await act(async () => {
      exportButton!.click()
      await Promise.resolve()
    })
    expect(exportMcpConfiguration).toHaveBeenCalled()
  })

  it('连接卡片列表：状态/类型徽章、工具数与最近检查，治理面板已移除', async () => {
    const container = await mountPage()

    expect(container.textContent).toContain('zhihu-open-api')
    expect(container.textContent).toContain('orders-rest')
    expect(container.textContent).toContain('MCP · Streamable HTTP')
    expect(container.textContent).toContain('HTTP API')
    expect(container.textContent).toContain('已连接')
    expect(container.textContent).toContain('连接异常')
    expect(container.textContent).toContain('上游 503')
    expect(container.textContent).toContain('查看详情')

    // 动态工具的审核/授权/测试调用已迁往 /tools：本页不再出现
    expect(container.textContent).not.toContain('审核通过并生效')
    expect(container.textContent).not.toContain('执行测试调用')
    expect(container.textContent).not.toContain('授权产品入口')
  })

  it('连接详情抽屉：概览凭据/传输，已发现工具行含前往 Tool 治理链接', async () => {
    const container = await mountPage()

    const detailButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('查看详情'))
    expect(detailButton).toBeTruthy()
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })

    // 概览：凭据 configured+last4、传输方式、发现版本
    expect(container.textContent).toContain('Bearer ****6789')
    expect(container.textContent).toContain('MCP · Streamable HTTP')
    expect(container.textContent).toContain('v3')

    clickDrawerTab(container, '已发现工具')
    await act(async () => { await Promise.resolve() })
    expect(container.textContent).toContain('get_answers')
    expect(container.textContent).toContain('R1')
    const link = container.querySelector<HTMLAnchorElement>('a[href="/tools?q=zhihu.get_answers"]')
    expect(link?.textContent).toContain('前往 Tool 治理')
  })

  it('运行健康与版本历史为置灰壳位（data-stub=demo，元素 disabled）', async () => {
    const container = await mountPage()

    const detailButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('查看详情'))
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })

    clickDrawerTab(container, '运行健康')
    await act(async () => { await Promise.resolve() })
    expect(container.textContent).toContain('主动健康监测属下一批')
    expect(container.textContent).toContain('自动退避重连策略')
    expect(container.querySelectorAll('[data-stub="demo"]').length).toBeGreaterThan(0)
    const faultButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('模拟断线'))
    expect(faultButton?.disabled).toBe(true)

    clickDrawerTab(container, '版本历史')
    await act(async () => { await Promise.resolve() })
    expect(container.textContent).toContain('配置版本时间线属下一批')
    expect(container.textContent).toContain('v3')
  })

  it('凭据管理 Tab：只读表格派生真实数据，「前往绑定」打开配置 Tab', async () => {
    const container = await mountPage('tab=credentials')

    expect(container.textContent).toContain('凭据管理')
    expect(container.textContent).toContain('静态 Bearer Token')
    expect(container.textContent).toContain('已配置 ****6789')
    expect(container.textContent).toContain('未配置')

    const bindButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('前往绑定'))
    expect(bindButton).toBeTruthy()
    await act(async () => {
      bindButton!.click()
      await Promise.resolve()
    })

    // 打开该连接详情的配置 Tab：编辑表单 + 危险操作（删除入口保留）
    expect(container.textContent).toContain('保存修改')
    expect(container.textContent).toContain('删除连接')
  })

  it('抽屉概览动作：测试健康 / 重新发现 / 启停连接（沿用现有 API）', async () => {
    const container = await mountPage()

    const detailButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('查看详情'))
    await act(async () => {
      detailButton!.click()
      await Promise.resolve()
    })

    const testButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('测试健康'))
    expect(testButton).toBeTruthy()
    await act(async () => {
      testButton!.click()
      await Promise.resolve()
    })
    expect(testMcpConnection).toHaveBeenCalledWith('conn-1')

    const discoverButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('重新发现'))
    expect(discoverButton).toBeTruthy()

    const toggleButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('停用连接'))
    expect(toggleButton).toBeTruthy()
  })

  it('抽屉配置 Tab 打开时点「新建连接」仍走 create（回归：编辑/新建显式分路）', async () => {
    const container = await mountPage('tab=credentials')

    // 打开某连接的配置 Tab（制造 drawerTab=config 的状态）
    const bindButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('前往绑定'))
    await act(async () => {
      bindButton!.click()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('保存修改')

    // 不关抽屉，直接点顶部「新建连接」并提交
    const newButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('新建连接'))
    await act(async () => {
      newButton!.click()
      await Promise.resolve()
    })

    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    const nameInput = Array.from(container.querySelectorAll<HTMLInputElement>('input'))
      .find((el) => el.placeholder === '如 zhihu-open-api')
    const urlInput = Array.from(container.querySelectorAll<HTMLInputElement>('input'))
      .find((el) => el.placeholder === 'https://example.com/mcp')
    expect(nameInput).toBeTruthy()
    expect(urlInput).toBeTruthy()
    await act(async () => {
      nativeSetter!.call(nameInput!, 'new-conn')
      nameInput!.dispatchEvent(new Event('input', { bubbles: true }))
      nativeSetter!.call(urlInput!, 'https://new.example.com/mcp')
      urlInput!.dispatchEvent(new Event('input', { bubbles: true }))
      await Promise.resolve()
    })
    const createButton = Array.from(container.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('创建连接'))
    expect(createButton).toBeTruthy()
    await act(async () => {
      createButton!.click()
      await Promise.resolve()
    })

    expect(createMcpConnection).toHaveBeenCalled()
    expect(updateMcpConnection).not.toHaveBeenCalled()
  })
})
