/** navConfig 回归测试 — 管理端五组导航的完整性与拆分边界（2026-09-21 P5 治理） */
import { describe, it, expect, afterEach } from 'vitest'
import { NAV, visibleNav } from './navConfig'

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

  it('五组结构齐全（总览直达 + 知识库/业务分析/运维监控/平台管理），旧分组名不再出现', () => {
    const labels = NAV.map((e) => e.label)
    for (const group of ['知识库', '业务分析', '运维监控', '平台管理']) {
      expect(labels, `缺少分组「${group}」`).toContain(group)
    }
    expect(labels).toContain('运营总览')
    // P5 收敛：旧分组名全部退役
    for (const oldGroup of ['知识运营', '可观测', '成本治理', '质量与配置', '审批与安全', '自动化']) {
      expect(labels, `旧分组「${oldGroup}」应已并入新五组`).not.toContain(oldGroup)
    }
  })

  it('核心路由不因导航重构丢失（追踪/入库/审批/网关安全/选品/告警/任务/访问控制）', () => {
    for (const p of [
      '/observability/traces',
      '/knowledge/documents',
      '/knowledge/pending',
      '/approvals',
      '/observability/gateway',
      '/selection-decision',
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

  it('P5 新分组归属正确（评测/反馈入知识库，定时任务/库存工单入运维监控，预算/模型入平台管理）', () => {
    const groupOf = (path: string) =>
      NAV.find((e) => (e.items ?? []).some((i) => i.path === path))?.label

    expect(groupOf('/evaluations')).toBe('知识库')
    expect(groupOf('/evaluations/feedback')).toBe('知识库')
    expect(groupOf('/schedules')).toBe('运维监控')
    expect(groupOf('/alerts')).toBe('运维监控')
    expect(groupOf('/cost-governance/budgets')).toBe('平台管理')
    expect(groupOf('/settings/models')).toBe('平台管理')
  })

  it('模型与供应商入口仅 admin 可见，旧模型价格入口不再出现在导航', () => {
    const platform = NAV.find((entry) => entry.label === '平台管理')
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
    expect(labels()).toContain('知识库')
    expect(labels()).toContain('平台管理')
    for (const p of ['/approvals', '/settings/access', '/settings/models']) {
      const item = NAV.flatMap((e) => e.items ?? []).find((i) => i.path === p)
      expect(item?.minRole, `${p} 应声明 minRole=admin`).toBe('admin')
    }
  })

  it('viewer 只看免角色分组（总览/业务分析/运维监控）', () => {
    loginAs('viewer')
    expect(labels()).toEqual(expect.arrayContaining(['运营总览', '业务分析', '运维监控']))
    for (const hidden of ['知识库', '平台管理']) {
      expect(labels(), `viewer 不应看到「${hidden}」`).not.toContain(hidden)
    }
  })

  it('未登录（无角色缓存）只看免角色分组，且不抛错', () => {
    loginAs(null)
    expect(labels()).not.toContain('平台管理')
  })

  it('minRole 声明与后端 RBAC 同语义（知识库/平台管理=editor，admin 条目=admin）', () => {
    for (const e of NAV) {
      if (e.label === '知识库' || e.label === '平台管理') expect(e.minRole).toBe('editor')
    }
    for (const path of ['/settings/access', '/approvals', '/settings/models']) {
      const item = NAV.flatMap((e) => e.items ?? []).find((i) => i.path === path)
      expect(item?.minRole, `${path} 应为 admin 专属`).toBe('admin')
    }
  })
})
