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
 * - 2026-09-21 三次收敛（三端拆分）：用户端只剩「AI 对话」单组。
 *   /agent/tasks、/reports、/alerts 页面目录与导航项一并移除（报告中心归
 *   管理端业务分析组、告警工单归管理端审批与安全组），核心路由断言收缩到
 *   仅 /agent；管理端路由黑名单不变。
 * - 2026-09-30 四次追加：新增顶层「旅游规划」→ /travel（页面 09-22 已建成，
 *   但从未挂进导航）。**不违反 09-21 收敛本意** —— 收敛针对的是运营/管理后台
 *   （知识库/竞品/选品/告警），旅游规划属于消费者业务功能，且建于收敛次日。
 *   故本文件「单组」断言相应放宽为「AI 对话 + 旅游规划」，管理端黑名单不变。
 * - 2026-10-01 五次收敛：「AI 对话」组（子项 智能问答 / 智能客服）压平为顶层
 *   直达「AI 助手」→ /agent。**子项数从 2 变 0 属有意为之**：智能客服那条是
 *   /agent?cs=1 的同页带参变体（不是独立路由），且 /agent 顶栏已有常驻客服
 *   胶囊入口；一个组只挂一个真实页面等于白白多一层展开。本文件相应新增
 *   「侧栏不得再出现同义重复入口」断言，锁住这次收敛不被回退。
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
