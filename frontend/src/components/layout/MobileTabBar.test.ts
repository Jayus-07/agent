/**
 * MobileTabBar 回归测试 — 移动断点 tab 高亮判定（UX P2-⑨）
 *
 * 项目惯例：无 testing-library，纯函数导出直测。
 */
import { describe, it, expect } from 'vitest'
import { isTabActive, MOBILE_TABS } from './MobileTabBar'

const agent = MOBILE_TABS.find((t) => t.path === '/agent')!
const tasks = MOBILE_TABS.find((t) => t.path === '/agent/tasks')!
const reports = MOBILE_TABS.find((t) => t.path === '/reports')!
const alerts = MOBILE_TABS.find((t) => t.path === '/alerts')!

describe('isTabActive — 底部四 tab 高亮', () => {
  it('对话 tab 为 exact 匹配：/agent 命中，/agent/tasks 不命中', () => {
    expect(isTabActive('/agent', agent)).toBe(true)
    expect(isTabActive('/agent/tasks', agent)).toBe(false)
  })

  it('任务/报告/我的告警为 prefix 匹配：自身与子路由命中', () => {
    expect(isTabActive('/agent/tasks', tasks)).toBe(true)
    expect(isTabActive('/reports', reports)).toBe(true)
    expect(isTabActive('/reports/rpt_123', reports)).toBe(true)
    expect(isTabActive('/alerts', alerts)).toBe(true)
  })

  it('前缀陷阱：/agents 不误命中 /agent（prefix 判定按 path/ 边界）', () => {
    expect(isTabActive('/agents', agent)).toBe(false)
    expect(isTabActive('/alerts-extra', alerts)).toBe(false)
  })

  it('未知路由四 tab 全灭（无高亮即无误导）', () => {
    for (const tab of MOBILE_TABS) {
      expect(isTabActive('/nowhere', tab)).toBe(false)
    }
  })

  it('四 tab 契约：对话/任务/报告/我的告警（设计文档 §4.1 拍板项）', () => {
    expect(MOBILE_TABS.map((t) => t.label)).toEqual(['对话', '任务', '报告', '我的告警'])
  })
})
