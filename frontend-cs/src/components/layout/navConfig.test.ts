/**
 * navConfig 回归测试 — 客服端（坐席工作台）导航完整性与三端拆分边界
 *
 * 2026-09-21 三端拆分：客服端只承载智能客服域，平台治理类入口
 * 全部留在管理端、业务对话留在用户端，两边都不得回渗。
 */
import { describe, it, expect } from 'vitest'
import { NAV, visibleNav } from './navConfig'

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

  it('客服端导航 = 工作台 + 人工接入 + 会话管理 + 满意度统计', () => {
    const labels = NAV.map((e) => e.label)
    expect(labels).toEqual(['工作台', '人工接入', '会话管理', '满意度统计'])
    for (const p of ['/cs', '/cs/handoff', '/cs/conversations', '/cs/stats']) {
      expect(allPaths, `客服端核心路由 ${p} 丢失`).toContain(p)
    }
  })

  it('客服端不出现平台治理入口（管理端专属）', () => {
    const labels = NAV.map((e) => e.label)
    for (const adminOnly of [
      '运营总览', '知识运营', '业务分析', '可观测',
      '质量与配置', '运营干预', '自动化', '成本治理',
    ]) {
      expect(labels, `管理端入口「${adminOnly}」不应出现在客服端`).not.toContain(adminOnly)
    }
    for (const p of allPaths) {
      expect(
        p.startsWith('/knowledge') || p.startsWith('/observability') ||
        p.startsWith('/prompts') || p.startsWith('/approvals') ||
        p.startsWith('/settings') || p.startsWith('/competitors') ||
        p.startsWith('/selection') || p.startsWith('/tasks') ||
        p.startsWith('/agent') || p.startsWith('/reports'),
        `管理端/用户端路由 ${p} 不应出现在客服端`,
      ).toBe(false)
    }
  })

  it('所有客服路由都在 /cs 前缀下', () => {
    for (const p of allPaths) {
      expect(p.startsWith('/cs'), `客服端路由 ${p} 应位于 /cs 前缀下`).toBe(true)
    }
  })
})

describe('visibleNav — 角色过滤', () => {
  it('baseline 返回无门槛全量菜单', () => {
    expect(visibleNav(true).length).toBe(NAV.length)
  })

  it('MVP 阶段无 minRole，过滤后仍为全量', () => {
    expect(NAV.every((e) => !e.minRole)).toBe(true)
    expect(visibleNav().length).toBe(NAV.length)
  })
})
