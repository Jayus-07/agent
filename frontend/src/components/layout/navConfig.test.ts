/** navConfig 回归测试：检查用户端导航路径、分组和重复入口边界。 */

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

  it('用户端导航保留独立旅游入口，未完成的选品产品不挂导航', () => {
    const labels = NAV.map((e) => e.label)
    expect(labels).toEqual(['AI 助手', '旅游规划'])
    for (const p of ['/agent', '/travel']) {
      expect(allPaths, `用户端核心路由 ${p} 丢失`).toContain(p)
    }
    expect(allPaths, '选品漏斗应由独立产品前端承载，完成前不出现在用户导航').not.toContain('/selection-funnel')
    // 已裁撤页面不得回渗（页面目录已删，导航也不得再挂）
    for (const p of ['/agent/tasks', '/reports', '/alerts']) {
      expect(allPaths, `已裁撤路由 ${p} 不应出现在用户端`).not.toContain(p)
    }
  })

  it('「AI 助手」为顶层直达入口 → /agent，不再有子项（2026-10-01 五次收敛）', () => {
    const entry = NAV.find((e) => e.label === 'AI 助手')
    expect(entry, '「AI 助手」顶层入口丢失').toBeTruthy()
    expect(entry?.path).toBe('/agent')
    expect(entry?.items ?? []).toHaveLength(0)
  })

  it('助手导航不混入其他助手，也不重复主入口', () => {
    // /agent 只承载企业助手；其他助手不能作为侧栏子入口混入。
    const labels = NAV.map((e) => e.label)
    for (const dup of ['AI 对话', '智能问答', '智能客服']) {
      expect(labels, `同义重复入口「${dup}」不应出现在用户端侧栏`).not.toContain(dup)
    }
    const childLabels = NAV.flatMap((e) => (e.items ?? []).map((i) => i.label))
    expect(childLabels).toEqual([])
  })

  it('「旅游规划」为顶层直达入口 → /travel（2026-09-30 四次追加）', () => {
    const entry = NAV.find((e) => e.label === '旅游规划')
    expect(entry, '「旅游规划」顶层入口丢失').toBeTruthy()
    expect(entry?.path).toBe('/travel')
    // 顶层直达：页面自身承载表单 + 结果（逐日时间轴/地图/导出），不需要子项
    expect(entry?.items ?? []).toHaveLength(0)
  })

  it('管理端专属入口不回渗（运维/运营/业务配置页面 2026-09-16 起全部在 frontend-admin）', () => {
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
