/**
 * navConfig 回归测试 — 用户端导航边界
 *
 * 演进记录：
 * - 2026-09-16 二次收敛：仅 AI 对话 + 报告中心（管理端专属入口全部回 admin）
 * - 2026-09-17 UX P1（docs/2026-09-17-UX体验架构设计.md §4.1，衔接 v3 ADR-001）：
 *   /alerts 判归 workspace 单组，用户端新增「我的告警」只读入口（工单流转操作仍在
 *   管理端 frontend-admin /alerts「告警中心」）。故 /alerts 从「管理端专属路由」
 *   黑名单中移出，但管理端入口标签「告警中心」仍禁止回渗。
 * - 2026-09-17 UX P1-⑤（X7 收尾）：「AI 对话」组新增子项「智能客服」→
 *   /agent?cs=1 自动滑出 CSDrawer。语义区分：用户端「智能客服」= CSDrawer
 *   消费者直达入口（子项，非顶层）；管理端「智能客服」入口与 /cs 坐席
 *   路由仍禁止出现在用户端（黑名单不变）。
 */
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

  it('用户端导航 = AI 对话 + 报告中心 + 我的告警（ADR-001 workspace 单组）', () => {
    const labels = NAV.map((e) => e.label)
    expect(labels).toEqual(['AI 对话', '报告中心', '我的告警'])
    for (const p of ['/agent', '/agent/tasks', '/reports', '/alerts']) {
      expect(allPaths, `用户端核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('AI 对话组含「智能客服」直达子项 → /agent?cs=1（UX P1-⑤）', () => {
    const chat = NAV.find((e) => e.label === 'AI 对话')
    const cs = chat?.items?.find((i) => i.label === '智能客服')
    expect(cs, '「智能客服」直达子项丢失').toBeTruthy()
    expect(cs?.path).toBe('/agent?cs=1')
  })

  it('管理端专属入口不回渗（运维/运营/业务配置页面 2026-09-16 起全部在 frontend-admin）', () => {
    // 黑名单只锁顶层入口：用户端子项「智能客服」是 CSDrawer 消费者直达
    // （/agent?cs=1），与管理端「智能客服」（坐席工作台）语义不同。
    const labels = NAV.map((e) => e.label)
    for (const adminOnly of [
      '数据驾驶舱', 'RAG 知识库', '告警中心',
      '竞品监控', '智能选品', '选品决策',
    ]) {
      expect(labels, `管理端入口「${adminOnly}」不应出现在用户端`).not.toContain(adminOnly)
    }
    for (const p of allPaths) {
      expect(
        p.startsWith('/knowledge') || p.startsWith('/cs') ||
        p.startsWith('/competitors') || p.startsWith('/selection'),
        `管理端路由 ${p} 不应出现在用户端`,
      ).toBe(false)
    }
  })
})
