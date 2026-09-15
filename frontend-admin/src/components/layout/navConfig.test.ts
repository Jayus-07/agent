/** navConfig 回归测试 — 管理端六组导航的完整性与拆分边界（2026-09-16 Phase 1.5） */
import { describe, it, expect } from 'vitest'
import { NAV } from './navConfig'

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
      '/cs',
      '/cs/conversations',
      '/alerts',
      '/schedules',
    ]) {
      expect(allPaths, `核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('管理端导航不含用户端独有入口（拆分边界不回渗）', () => {
    // 2026-09-16 二次收敛：知识运营/竞品/选品/客服/告警工单全部归管理端；
    // 报告中心两端并存（管理端在「业务分析」组）。用户端独有仅 AI 对话。
    const labels = NAV.map((e) => e.label)
    for (const userOnly of ['AI 对话', '智能问答']) {
      expect(labels).not.toContain(userOnly)
    }
    for (const p of allPaths) {
      expect(p.startsWith('/agent'), `用户端独有路由 ${p} 不应出现在管理端`).toBe(false)
    }
  })
})
