/**
 * MobileTabBar 回归测试 — 移动断点 tab 高亮判定（UX P2-⑨）
 *
 * 项目惯例：无 testing-library，纯函数导出直测。
 *
 * - 2026-09-30 修正（**跟随失效导航修复**）：原「底部四 tab（对话/任务/报告/
 *   我的告警）」中，后三个指向 /agent/tasks、/reports、/alerts —— 三个页面目录
 *   已在 2026-09-21 三次收敛时删除，属**失效导航**（移动端点进去 404），
 *   而本文件当时仍在断言这三个 tab 存在 —— 测试在保护一段死代码。
 *   现断言随 MOBILE_TABS 收敛为「AI 助手 + 旅游规划」，并**新增一条防复发边界**
 *   （tab 不得指向已裁撤路由），避免同类问题再次静默发生。
 * - 2026-10-01 五次收敛：「对话」→「AI 助手」，跟随 navConfig 侧栏文案。
 */
import { describe, it, expect } from 'vitest'
import { isTabActive, MOBILE_TABS, CS_TAB_LABEL } from './MobileTabBar'

const agent = MOBILE_TABS.find((t) => t.path === '/agent')!
const travel = MOBILE_TABS.find((t) => t.path === '/travel')!

describe('isTabActive — 底部 tab 高亮', () => {
  it('AI 助手 tab 为 exact 匹配：/agent 命中，/agent/tasks 不命中', () => {
    expect(isTabActive('/agent', agent)).toBe(true)
    expect(isTabActive('/agent/tasks', agent)).toBe(false)
  })

  it('旅游规划 tab 为 prefix 匹配：自身与子路由命中', () => {
    expect(isTabActive('/travel', travel)).toBe(true)
    expect(isTabActive('/travel/result', travel)).toBe(true)
  })

  it('前缀陷阱：/agents 不误命中 /agent，/travellers 不误命中 /travel（按 path/ 边界）', () => {
    expect(isTabActive('/agents', agent)).toBe(false)
    expect(isTabActive('/travellers', travel)).toBe(false)
  })

  it('未知路由所有 tab 全灭（无高亮即无误导）', () => {
    for (const tab of MOBILE_TABS) {
      expect(isTabActive('/nowhere', tab)).toBe(false)
    }
  })

  it('tab 契约：链接型 tab = AI 助手 + 旅游规划（与 navConfig 用户端边界一致）', () => {
    expect(MOBILE_TABS.map((t) => t.label)).toEqual(['AI 助手', '旅游规划'])
  })

  it('智能客服升为第三 tab（2026-10-07）：按钮型、非链接，不进 MOBILE_TABS 路由契约', () => {
    expect(CS_TAB_LABEL).toBe('智能客服')
    // 防复发：它不是路由 tab，不得以 path 形式混进链接型数组
    expect(MOBILE_TABS.map((t) => t.path)).not.toContain('/agent?cs=1')
  })

  it('tab 不得指向已裁撤路由（2026-09-30 新增边界，防失效导航复发）', () => {
    const paths = MOBILE_TABS.map((t) => t.path)
    for (const dead of ['/agent/tasks', '/reports', '/alerts']) {
      expect(paths, `已裁撤路由 ${dead} 不应出现在移动端 tab`).not.toContain(dead)
    }
  })
})
