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

  it('用户端导航 = AI 助手 + 旅游规划 + 选品漏斗（2026-10-06 六次追加：多域隔离 M4 第四扇门）', () => {
    const labels = NAV.map((e) => e.label)
    expect(labels).toEqual(['AI 助手', '旅游规划', '选品漏斗'])
    for (const p of ['/agent', '/travel', '/selection-funnel']) {
      expect(allPaths, `用户端核心路由 ${p} 丢失`).toContain(p)
    }
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

  it('侧栏不得回退成同义重复入口（智能问答/智能客服/对话 组，2026-10-01 新增边界）', () => {
    // 三个词指同一件事：/agent 的对话主入口。重复入口会让用户以为它们是
    // 不同功能；智能客服的常驻入口在 /agent 顶栏胶囊（ChatHeader onOpenCS），
    // 不靠侧栏承载。此断言防的就是「有人又把子项加回去」。
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
    // 黑名单只锁顶层入口；用户端 /agent 顶栏的「智能客服」胶囊是 CSDrawer
    // 消费者入口（不经过 navConfig），与管理端「智能客服」（坐席工作台）语义不同。
    const labels = NAV.map((e) => e.label)
    for (const adminOnly of [
      '数据驾驶舱', 'RAG 知识库', '告警中心',
      '竞品监控', '智能选品', '选品决策',
    ]) {
      expect(labels, `管理端入口「${adminOnly}」不应出现在用户端`).not.toContain(adminOnly)
    }
    for (const p of allPaths) {
      // /selection-funnel 是 2026-10-06 多域隔离 M4 拍板的用户端专属页例外
      // （漏斗报告落地页，主图引导卡带参跳转目标）；其余 /selection 管理端
      // 路由照旧禁止回渗。
      if (p === '/selection-funnel') continue
      expect(
        p.startsWith('/knowledge') || p.startsWith('/cs') ||
        p.startsWith('/competitors') || p.startsWith('/selection'),
        `管理端路由 ${p} 不应出现在用户端`,
      ).toBe(false)
    }
  })
})
