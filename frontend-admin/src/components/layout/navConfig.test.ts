/** navConfig 回归测试 — 管理端五组导航的完整性与拆分边界（2026-09-21 P5 治理） */
import { describe, it, expect, afterEach } from 'vitest'
import { NAV, getActiveNavLabel, isNavPathActive, visibleNav } from './navConfig'

const allPaths = NAV.flatMap((e) => [e.path, ...(e.items ?? []).map((i) => i.path)]).filter(
  (p): p is string => Boolean(p),
)

describe('NAV — 导航配置完整性', () => {
  it('每个入口要么有 path，要么有非空 items', () => {
    for (const entry of NAV) {
      expect(entry.label, '入口缺少 label').toBeTruthy()
      const hasPath = Boolean(entry.path)
      const hasItems = (entry.items?.length ?? 0) > 0
      expect(hasPath || hasItems, `入口「${entry.label}」既无 path 也无 items`).toBe(true)
    }
  })

  it('所有路径非空且全局不重复', () => {
    expect(allPaths.every((p) => p.startsWith('/'))).toBe(true)
    expect(new Set(allPaths).size).toBe(allPaths.length)
  })

  it('五组结构齐全（总览直达 + 业务运营/内容与质量/运行中心/系统设置）', () => {
    const labels = NAV.map((e) => e.label)
    for (const group of ['业务运营', '内容与质量', '运行中心', '系统设置']) {
      expect(labels, `缺少分组「${group}」`).toContain(group)
    }
    expect(labels).toContain('运营总览')
    for (const oldGroup of ['知识库', '业务分析', '运维监控', '平台管理']) {
      expect(labels, `旧分组「${oldGroup}」应已并入新五组`).not.toContain(oldGroup)
    }
  })

  it('核心工作台和系统路由不因导航重构丢失', () => {
    for (const p of [
      '/observability/monitoring',
      '/knowledge/workbench',
      '/evaluations/center',
      '/approvals',
      '/selection-workbench',
      '/reports',
      '/competitors',
      '/alerts',
      '/schedules',
      // B13 能力治理只读页（2026-09-16）
      '/agents',
      '/skills',
      '/settings/access',
    ]) {
      expect(allPaths, `核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('业务入口归类正确，且同组页面用二级分组表达工作边界', () => {
    const groupOf = (path: string) =>
      NAV.find((e) => (e.items ?? []).some((i) => i.path === path))?.label

    expect(groupOf('/selection-workbench')).toBe('业务运营')
    expect(groupOf('/knowledge/workbench')).toBe('内容与质量')
    expect(groupOf('/evaluations/center')).toBe('内容与质量')
    expect(groupOf('/observability/monitoring')).toBe('运行中心')
    expect(groupOf('/schedules')).toBe('运行中心')
    expect(groupOf('/alerts')).toBe('运行中心')
    expect(groupOf('/cost-governance/budgets')).toBe('系统设置')
    expect(groupOf('/settings/models')).toBe('系统设置')

    const content = NAV.find((entry) => entry.label === '内容与质量')
    expect(content?.items?.find((item) => item.path === '/knowledge/workbench')?.section).toBe('知识内容')
    expect(content?.items?.find((item) => item.path === '/evaluations/center')?.section).toBe('评测治理')
    expect(content?.items?.find((item) => item.path === '/evaluations/center')?.minRole).toBe('admin')

    const operations = NAV.find((entry) => entry.label === '运行中心')
    expect(operations?.items?.find((item) => item.path === '/observability/monitoring')?.section).toBe('运行状态')

    const platform = NAV.find((entry) => entry.label === '系统设置')
    expect(platform?.items?.find((item) => item.path === '/prompts')?.section).toBe('AI 能力')
    expect(platform?.items?.find((item) => item.path === '/settings/access')?.section).toBe('权限与模型')
  })

  it('模型与供应商入口仅 admin 可见，旧模型价格入口不再出现在导航', () => {
    const platform = NAV.find((entry) => entry.label === '系统设置')
    expect(platform?.items).toEqual(expect.arrayContaining([
      expect.objectContaining({ label: '模型与供应商', path: '/settings/models', minRole: 'admin' }),
    ]))
    expect(allPaths).not.toContain('/cost-governance/prices')
  })

  it('管理端导航不含用户端独有入口（拆分边界不回渗）', () => {
    // 2026-09-16 二次收敛：知识运营/竞品/选品/客服/告警工单全部归管理端；
    // 报告中心两端并存（管理端在「业务分析」组）。用户端独有仅 AI 对话。
    const labels = NAV.map((e) => e.label)
    for (const userOnly of ['AI 对话', '智能问答']) {
      expect(labels).not.toContain(userOnly)
    }
    for (const p of allPaths) {
      // 边界是用户端 AI 对话入口 /agent（含子路径）。C11 起管理端另有顶层
      // /agents（Agent 节点总览），不能再用 startsWith('/agent') 一刀切。
      const isUserAgentRoute = p === '/agent' || p.startsWith('/agent/')
      expect(isUserAgentRoute, `用户端独有路由 ${p} 不应出现在管理端`).toBe(false)
    }
  })

  it('客服域不回渗（2026-09-21 三端拆分：/cs/* 全部迁往 frontend-cs）', () => {
    for (const p of allPaths) {
      expect(p.startsWith('/cs'), `客服端路由 ${p} 不应出现在管理端`).toBe(false)
    }
    const labels = NAV.map((e) => e.label)
    for (const csOnly of ['客服对话', '客服会话', '人工接入坐席']) {
      expect(labels, `客服端入口「${csOnly}」不应出现在管理端`).not.toContain(csOnly)
    }
  })

  it('工作台合并后不再暴露旧列表入口，但旧 URL 仍由兼容路由承接', () => {
    const labels = NAV.flatMap((entry) => entry.items ?? []).map((item) => item.label)
    for (const label of ['选品漏斗', '选品决策', '文档入库', '待复核', '评测结果', '问答追踪', 'Token 用量']) {
      expect(labels, '旧入口「' + label + '」不应继续出现在侧栏').not.toContain(label)
    }
    expect(labels).toEqual(expect.arrayContaining(['选品工作台', '知识库工作台', '评测中心', '运行监控工作台']))
    for (const oldPath of ['/selection-funnel', '/selection-decision', '/knowledge/documents', '/evaluations', '/observability/traces']) {
      expect(allPaths).not.toContain(oldPath)
    }
  })
})

describe('visibleNav — 按角色过滤（2026-09-16 角色硬闸的 UI 层）', () => {
  const labels = () => visibleNav().map((e) => e.label)

  function loginAs(role: string | null) {
    sessionStorage.clear()
    if (role) sessionStorage.setItem('agent.user_info', JSON.stringify({ userId: 1, roles: [role] }))
  }

  afterEach(() => sessionStorage.clear())

  it('admin 看到全部分组', () => {
    loginAs('admin')
    expect(labels()).toEqual(NAV.map((e) => e.label))
  })

  it('editor 无 admin 专属条目声明（工具审批/访问控制/模型），组本身可见', () => {
    // item 级 minRole 是声明元数据（可见性由后端 403 兜底），visibleNav
    // 只过滤组级 minRole —— 此处校验声明本身，防止 admin 条目漏标。
    loginAs('editor')
    expect(labels()).toContain('内容与质量')
    expect(labels()).toContain('系统设置')
    for (const p of ['/approvals', '/settings/access', '/settings/models']) {
      const item = NAV.flatMap((e) => e.items ?? []).find((i) => i.path === p)
      expect(item?.minRole, `${p} 应声明 minRole=admin`).toBe('admin')
    }
  })

  it('viewer 只看免角色分组（总览/业务运营/运行中心）', () => {
    loginAs('viewer')
    expect(labels()).toEqual(expect.arrayContaining(['运营总览', '业务运营', '运行中心']))
    for (const hidden of ['内容与质量', '系统设置']) {
      expect(labels(), `viewer 不应看到「${hidden}」`).not.toContain(hidden)
    }
  })

  it('未登录（无角色缓存）只看免角色分组，且不抛错', () => {
    loginAs(null)
    expect(labels()).not.toContain('平台管理')
  })

  it('minRole 声明与后端 RBAC 同语义（内容与质量/系统设置=editor，admin 条目=admin）', () => {
    for (const e of NAV) {
      if (e.label === '内容与质量' || e.label === '系统设置') expect(e.minRole).toBe('editor')
    }
    for (const path of ['/settings/access', '/approvals', '/settings/models']) {
      const item = NAV.flatMap((e) => e.items ?? []).find((i) => i.path === path)
      expect(item?.minRole, `${path} 应为 admin 专属`).toBe('admin')
    }
  })

  it('按当前路径定位所属一级菜单，深层页面可自动展开正确分组', () => {
    expect(getActiveNavLabel('/')).toBe('运营总览')
    expect(getActiveNavLabel('/knowledge/workbench')).toBe('内容与质量')
    expect(getActiveNavLabel('/knowledge/operations/traces/trace-1')).toBe('内容与质量')
    expect(getActiveNavLabel('/observability/traces/trace-1')).toBe('运行中心')
    expect(getActiveNavLabel('/settings/access')).toBe('系统设置')
    expect(getActiveNavLabel('/not-found')).toBeUndefined()
    expect(isNavPathActive('/observability/traces/trace-1', '/observability/monitoring', ['/observability/traces'])).toBe(true)
    expect(isNavPathActive('/observability/traces-other', '/observability/traces')).toBe(false)
  })
})
