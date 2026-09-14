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

  it('智能问答入口存在（聊天页主路由不因导航重构丢失）', () => {
    const aiGroup = NAV.find((e) => e.label === 'AI 对话')
    expect(aiGroup?.items?.some((i) => i.path === '/agent')).toBe(true)
  })
})
