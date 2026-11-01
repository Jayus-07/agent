import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getCapabilities = vi.hoisted(() => vi.fn())
const getMcpServers = vi.hoisted(() => vi.fn())
const getMcpTools = vi.hoisted(() => vi.fn())
const atLeast = vi.hoisted(() => vi.fn())
const getDiscoveryPolicy = vi.hoisted(() => vi.fn())
const updateDiscoveryPolicy = vi.hoisted(() => vi.fn())
const simulateDiscovery = vi.hoisted(() => vi.fn())
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { push: vi.fn(), replace: vi.fn() },
}))

vi.mock('@/api/registry', () => ({ getCapabilities, getMcpServers, getMcpTools }))
vi.mock('@/api/discovery', () => ({ getDiscoveryPolicy, updateDiscoveryPolicy, simulateDiscovery }))
vi.mock('@/lib/auth', () => ({ atLeast }))
vi.mock('next/navigation', () => ({
  useRouter: () => navigation.router,
  usePathname: () => '/skills',
  useSearchParams: () => navigation.params,
}))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

import SkillsPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

beforeEach(() => {
  getCapabilities.mockResolvedValue({
    count: 0,
    routed_count: 0,
    capabilities: [],
    workflows: [],
    skills: [],
  })
  getMcpServers.mockResolvedValue({ servers: [] })
  getMcpTools.mockResolvedValue({ tools: [] })
  getDiscoveryPolicy.mockResolvedValue({
    policy: {
      id: '', name: '', active: false, mode: 'adaptive', top_k: 3,
      max_rounds: 2, cost_budget_milliunits: 50, timeout_ms: 8000,
      prompt_key: 'router.tool_selector', tenant_id: 'default',
      created_by: '', updated_by: '', created_at: '', updated_at: '',
    },
    policies: [],
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  navigation.params = new URLSearchParams()
  vi.clearAllMocks()
})

async function renderPage() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })

  await act(async () => {
    root.render(<SkillsPage />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  return container
}

async function setInputValue(selector: string, value: string) {
  const input = document.querySelector(selector) as HTMLInputElement
  await act(async () => {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype, 'value')?.set
    nativeSetter?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('Skill 页面 Tool 注册表入口', () => {
  it('Editor 不会看到只限管理员访问的 Tool 注册表链接', async () => {
    atLeast.mockReturnValue(false)

    const container = await renderPage()

    expect(container.querySelector('a[href="/tools"]')).toBeNull()
  })

  it('Admin 可以访问 Tool 注册表链接', async () => {
    atLeast.mockReturnValue(true)

    const container = await renderPage()

    expect(container.querySelector('a[href="/tools"]')).toBeTruthy()
  })
})

describe('Skill 页面 Tool Discovery 档（决策 #18，B1）', () => {
  const POLICY = {
    id: 'disc_pol_abc', name: '基线策略', active: true, mode: 'reasoning',
    top_k: 5, max_rounds: 2, cost_budget_milliunits: 80, timeout_ms: 12000,
    prompt_key: 'router.tool_selector', tenant_id: 'default',
    created_by: 'u1', updated_by: 'u1',
    created_at: '2026-10-28T00:00:00', updated_at: '2026-10-28T00:00:00',
  }

  it('策略表单按生效策略初始化，保存调用 updateDiscoveryPolicy', async () => {
    getDiscoveryPolicy.mockResolvedValue({ policy: POLICY, policies: [POLICY] })
    updateDiscoveryPolicy.mockResolvedValue({ ...POLICY, top_k: 8 })
    navigation.params = new URLSearchParams('tab=discovery')

    const container = await renderPage()
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })

    expect(document.body.textContent).toContain('发现策略')
    const modeSelect = document.querySelector(
      '[data-testid="discovery-policy-mode"]') as HTMLSelectElement
    expect(modeSelect.value).toBe('reasoning')
    const topK = document.querySelector(
      '[data-testid="discovery-policy-top_k"]') as HTMLInputElement
    expect(topK.value).toBe('5')

    await setInputValue('[data-testid="discovery-policy-top_k"]', '8')
    const save = document.querySelector(
      '[data-testid="discovery-policy-save"]') as HTMLButtonElement
    await act(async () => { save.click(); await new Promise((r) => setTimeout(r, 0)) })

    expect(updateDiscoveryPolicy).toHaveBeenCalled()
    const arg = updateDiscoveryPolicy.mock.calls[0][0]
    expect(arg.name).toBe('基线策略')
    expect(arg.mode).toBe('reasoning')
    expect(arg.top_k).toBe(8)
    expect(document.querySelector('[data-testid="discovery-notice"]')).not.toBeNull()
    expect(container).toBeTruthy()
  })

  it('模拟器输入问题后运行，渲染候选卡与权限说明；无结果时空态', async () => {
    simulateDiscovery.mockResolvedValue({
      query: '规划福州 2 天的行程', domain: 'travel', product_id: 'agent',
      candidates: [{
        name: 'travel.poi_search', risk: 'LOW', source: 'domain_registry',
        permission_note: '产品范围允许（product=agent，域=travel）',
      }],
      policy: POLICY, excluded_count: 2, candidate_count: 3, source: 'domain_registry',
      notes: ['2 个候选被产品/角色/范围过滤剔除'],
    })
    navigation.params = new URLSearchParams('tab=discovery')

    await renderPage()
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })

    expect(document.body.textContent).toContain('发现模拟器')
    expect(document.body.textContent).toContain('发现模拟结果') // 空态

    await setInputValue('[data-testid="discovery-sim-query"]', '规划福州 2 天的行程')
    await setInputValue('[data-testid="discovery-sim-domain"]', 'travel')
    const run = document.querySelector(
      '[data-testid="discovery-sim-run"]') as HTMLButtonElement
    await act(async () => { run.click(); await new Promise((r) => setTimeout(r, 0)) })

    expect(simulateDiscovery).toHaveBeenCalledWith({
      query: '规划福州 2 天的行程', domain: 'travel', top_k: 3,
    })
    expect(document.querySelectorAll('[data-testid="discovery-sim-candidate"]').length).toBe(1)
    expect(document.body.textContent).toContain('travel.poi_search')
    expect(document.body.textContent).toContain('产品范围允许')
  })
})

describe('Skill 页面能力清单 owner 负责人列（G1 治理）', () => {
  it('能力清单表展示「负责人」列与 owner 值', async () => {
    getCapabilities.mockResolvedValue({
      count: 1,
      routed_count: 1,
      capabilities: [{
        name: 'sql.query', skill: 'sql', routed: true, owner: 'platform-team',
        rule_keywords: [], examples: [], reason: '', registered: true,
      }],
      workflows: [],
      skills: [{ name: 'sql_skill', description: '', capabilities: ['sql.query'] }],
    })

    await renderPage()
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })

    expect(document.body.textContent).toContain('负责人')
    expect(document.body.textContent).toContain('platform-team')
  })
})
