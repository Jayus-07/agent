/** navConfig 回归测试 — 管理端六组导航的完整性与拆分边界（2026-09-16 Phase 1.5） */
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

  it('六组结构齐全（知识运营/业务分析/可观测/质量与配置/运营干预/自动化 + 总览直达）', () => {
    const labels = NAV.map((e) => e.label)
    for (const group of ['运营总览', '知识运营', '业务分析', '可观测', '质量与配置', '运营干预', '自动化']) {
      expect(labels, `缺少分组「${group}」`).toContain(group)
    }
  })

  it('核心路由不因导航重构丢失（追踪/入库/审批/网关安全/选品/客服/告警/任务）', () => {
    for (const p of [
      '/observability/traces',
      '/knowledge/documents',
      '/knowledge/pending',
      '/approvals',
      '/observability/gateway',
      '/selection-decision',
      '/reports',
      '/competitors',
      '/cs/conversations',
      '/alerts',
      '/schedules',
      // B13 能力治理只读页（2026-09-16）
      '/agents',
      '/skills',
    ]) {
      expect(allPaths, `核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('模型与供应商入口仅 admin 可见，旧模型价格入口不再出现在导航', () => {
    const quality = NAV.find((entry) => entry.label === '质量与配置')
    expect(quality?.items).toEqual(expect.arrayContaining([
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

  it('editor 无「运营干预」（处置权仅 admin），其余可见', () => {
    loginAs('editor')
    expect(labels()).not.toContain('运营干预')
    expect(labels()).toContain('质量与配置')
    expect(labels()).toContain('知识运营')
  })

  it('viewer 只看免角色分组（总览/业务分析/可观测/自动化）', () => {
    loginAs('viewer')
    expect(labels()).toEqual(expect.arrayContaining(['运营总览', '业务分析', '可观测', '自动化']))
    for (const hidden of ['知识运营', '质量与配置', '运营干预']) {
      expect(labels(), `viewer 不应看到「${hidden}」`).not.toContain(hidden)
    }
  })

  it('未登录（无角色缓存）只看免角色分组，且不抛错', () => {
    loginAs(null)
    expect(labels()).not.toContain('运营干预')
  })

  it('minRole 声明与后端 RBAC 同语义（知识运营/质量配置=editor，运营干预=admin）', () => {
    for (const e of NAV) {
      if (e.label === '知识运营' || e.label === '质量与配置') expect(e.minRole).toBe('editor')
      if (e.label === '运营干预') expect(e.minRole).toBe('admin')
    }
  })
})
