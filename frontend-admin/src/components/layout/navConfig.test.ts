/** navConfig 回归测试 — 控制台导航与聊天页应用菜单共用数据的完整性 */
import { describe, it, expect } from 'vitest'
import { NAV } from './navConfig'

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
    const paths: string[] = []
    for (const entry of NAV) {
      if (entry.path) paths.push(entry.path)
      for (const item of entry.items ?? []) {
        expect(item.path, `子项「${item.label}」缺少 path`).toBeTruthy()
        paths.push(item.path)
      }
    }
    expect(paths.every((p) => p.startsWith('/'))).toBe(true)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('链路追踪入口存在（管理端主路由不因导航重构丢失）', () => {
    const traceGroup = NAV.find((e) => e.label === '链路追踪')
    expect(traceGroup?.items?.some((i) => i.path === '/observability/traces')).toBe(true)
  })

  it('管理端导航不含用户端业务入口（拆分边界不回渗）', () => {
    const labels = NAV.map((e) => e.label)
    for (const userOnly of ['AI 对话', 'RAG 知识库', '报告中心', '智能选品']) {
      expect(labels).not.toContain(userOnly)
    }
    const paths = NAV.flatMap((e) => [e.path, ...(e.items ?? []).map((i) => i.path)]).filter((p): p is string => Boolean(p))
    for (const p of paths) {
      expect(p.startsWith('/agent') || p.startsWith('/reports') || p.startsWith('/selection')).toBe(false)
    }
  })
})
