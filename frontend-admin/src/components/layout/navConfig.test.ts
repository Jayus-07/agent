/** 管理端侧栏分类、兼容路由和权限声明回归测试。 */
import { afterEach, describe, expect, it } from 'vitest'
import { NAV, getActiveNavLabel, isNavItemActive, isNavPathActive, visibleNav } from './navConfig'

const items = NAV.flatMap((entry) => entry.items ?? [])
const allHrefs = NAV.flatMap((entry) => [
  ...(entry.path ? [entry.path] : []),
  ...(entry.items ?? []).map((item) => item.href ?? item.path),
])

function itemByLabel(label: string) {
  return items.find((item) => item.label === label)
}

function groupByItemLabel(label: string) {
  return NAV.find((entry) => entry.items?.some((item) => item.label === label))
}

describe('NAV — 管理端导航分类', () => {
  it('侧栏按六个业务标签组织，且每个入口都有可访问目标', () => {
    expect(NAV.map((entry) => entry.label)).toEqual([
      '运营总览', '业务运营', '内容与质量', '运行监控', '能力治理', '安全运维',
    ])
    for (const entry of NAV) {
      expect(entry.label, '入口缺少标签').toBeTruthy()
      expect(Boolean(entry.path) || (entry.items?.length ?? 0) > 0).toBe(true)
    }
    expect(allHrefs.every((href) => href.startsWith('/'))).toBe(true)
    expect(new Set(allHrefs).size).toBe(allHrefs.length)
  })

  it('工作台中的总览、执行记录、网关和 Token 入口分别指向对应视图', () => {
    const overview = itemByLabel('运行总览')
    const traces = itemByLabel('执行记录与 Trace')
    const gateway = itemByLabel('网关安全')
    const tokens = itemByLabel('Token 用量')

    expect(overview?.href).toBe('/observability/monitoring?tab=overview')
    expect(traces?.href).toBe('/observability/monitoring?tab=traces')
    expect(gateway?.href).toBe('/observability/monitoring?tab=gateway')
    expect(tokens?.href).toBe('/observability/monitoring?tab=tokens')
    expect(isNavItemActive('/observability/monitoring', overview!, '')).toBe(true)
    expect(isNavItemActive('/observability/monitoring', overview!, '?tab=traces')).toBe(false)
    expect(isNavItemActive('/observability/monitoring', traces!, '?tab=traces')).toBe(true)
    expect(isNavItemActive('/observability/monitoring', gateway!, '?tab=gateway')).toBe(true)
    expect(isNavItemActive('/observability/monitoring', tokens!, '?tab=tokens')).toBe(true)
    expect(isNavItemActive('/observability/traces/trace-1', traces!, '')).toBe(true)
  })

  it('所有管理端工作台都归入对应分组，兼容入口仍能定位到工作台', () => {
    const groupOf = (label: string) => groupByItemLabel(label)?.label
    expect(groupOf('选品工作台')).toBe('业务运营')
    expect(groupOf('知识库工作台')).toBe('内容与质量')
    expect(groupOf('评测中心')).toBe('内容与质量')
    expect(groupOf('运行总览')).toBe('运行监控')
    expect(groupOf('Agent 与工作流')).toBe('运行监控')
    expect(groupOf('Tool 治理')).toBe('能力治理')
    expect(groupOf('模型与供应商')).toBe('能力治理')
    expect(groupOf('Prompt 版本')).toBe('能力治理')
    expect(groupOf('访问控制')).toBe('安全运维')
    expect(groupOf('审批与审计')).toBe('安全运维')

    for (const route of [
      '/selection-funnel', '/selection-decision',
      '/knowledge', '/knowledge/documents', '/knowledge/pending', '/knowledge/upload-failures',
      '/knowledge/keywords', '/knowledge/operations', '/evaluations',
      '/evaluations/datasets', '/evaluations/feedback',
      '/observability/traces', '/observability/gateway', '/observability/tokens',
      '/cost-governance/prices',
    ]) {
      expect(items.some((item) => item.activePaths?.includes(route)), `兼容入口 ${route} 未关联`).toBe(true)
    }
  })

  it('业务、内容、治理和安全分组保留既有关键页面', () => {
    const paths = items.map((item) => item.path)
    for (const path of [
      '/data-explorer', '/competitors', '/selection-workbench', '/reports',
      '/knowledge/workbench', '/evaluations/center', '/observability/monitoring',
      '/agents', '/tasks', '/schedules', '/observability/selection-funnel',
      '/tools', '/skills', '/settings/models', '/cost-governance/budgets', '/prompts',
      '/consistency', '/releases', '/settings/access', '/approvals', '/security',
      '/observability/alerts', '/alerts',
    ]) {
      expect(paths, `导航缺少 ${path}`).toContain(path)
    }
  })

  it('能力治理、发布、审批及访问控制入口保留既有角色声明', () => {
    expect(groupByItemLabel('知识库工作台')?.minRole).toBe('editor')
    expect(NAV.find((entry) => entry.label === '能力治理')?.minRole).toBe('editor')
    for (const label of ['Tool 治理', '模型与供应商', '资产一致性检查', '发布记录', '访问控制', '审批与审计']) {
      expect(itemByLabel(label)?.minRole, `${label} 应保留 admin 声明`).toBe('admin')
    }
  })

  it('管理端不暴露登录入口为侧栏项目，也不混入用户端独有入口', () => {
    const labels = NAV.map((entry) => entry.label)
    for (const name of ['登录', 'AI 对话', '智能问答', '客服对话']) {
      expect(labels).not.toContain(name)
    }
    for (const href of allHrefs) {
      expect(href.startsWith('/cs'), `客服端路由 ${href} 不应出现在管理端`).toBe(false)
      expect(href === '/agent' || href.startsWith('/agent/')).toBe(false)
    }
  })
})

describe('visibleNav — 按角色过滤分组', () => {
  function loginAs(role: string | null) {
    sessionStorage.clear()
    if (role) sessionStorage.setItem('agent.user_info', JSON.stringify({ userId: 1, roles: [role] }))
  }

  afterEach(() => sessionStorage.clear())

  it('admin 可见全部分组', () => {
    loginAs('admin')
    expect(visibleNav().map((entry) => entry.label)).toEqual(NAV.map((entry) => entry.label))
  })

  it('viewer 仍可进入公开的监控与安全入口，编辑类分组保持隐藏', () => {
    loginAs('viewer')
    const labels = visibleNav().map((entry) => entry.label)
    expect(labels).toEqual(['运营总览', '业务运营', '运行监控', '安全运维'])
  })

  it('按深层路由自动定位侧栏分组，工作台子视图共享运行监控分组', () => {
    expect(getActiveNavLabel('/')).toBe('运营总览')
    expect(getActiveNavLabel('/knowledge/workbench')).toBe('内容与质量')
    expect(getActiveNavLabel('/knowledge/operations/traces/trace-1')).toBe('内容与质量')
    expect(getActiveNavLabel('/observability/traces/trace-1')).toBe('运行监控')
    expect(getActiveNavLabel('/observability/monitoring')).toBe('运行监控')
    expect(getActiveNavLabel('/settings/access')).toBe('安全运维')
    expect(getActiveNavLabel('/not-found')).toBeUndefined()
    expect(isNavPathActive('/observability/traces/trace-1', '/observability/monitoring', ['/observability/traces'])).toBe(true)
    expect(isNavPathActive('/observability/traces-other', '/observability/traces')).toBe(false)
  })
})
