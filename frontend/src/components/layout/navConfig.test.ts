/** navConfig 回归测试 — 用户端极简导航（2026-09-16 二次收敛：仅 AI 对话 + 报告中心） */
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

  it('用户端只保留 AI 对话 + 报告中心（二次收敛边界）', () => {
    const labels = NAV.map((e) => e.label)
    expect(labels).toEqual(['AI 对话', '报告中心'])
    for (const p of ['/agent', '/agent/tasks', '/reports']) {
      expect(allPaths, `用户端核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('管理端专属入口不回渗（运维/运营/业务配置页面 2026-09-16 起全部在 frontend-admin）', () => {
    const labels = NAV.map((e) => e.label)
    for (const adminOnly of [
      '数据驾驶舱', 'RAG 知识库', '智能客服', '告警中心',
      '竞品监控', '智能选品', '选品决策',
    ]) {
      expect(labels, `管理端入口「${adminOnly}」不应出现在用户端`).not.toContain(adminOnly)
    }
    for (const p of allPaths) {
      expect(
        p.startsWith('/knowledge') || p.startsWith('/cs') || p.startsWith('/alerts') ||
        p.startsWith('/competitors') || p.startsWith('/selection'),
        `管理端路由 ${p} 不应出现在用户端`,
      ).toBe(false)
    }
  })
})
