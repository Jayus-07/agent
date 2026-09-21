/**
 * EmptyState 回归测试 — 管理端统一空态（UX P2-⑧）
 *
 * 项目惯例：纯函数导出直测，无 testing-library。
 * 锁定设计硬约束：kind → 默认文案映射；可导航出路语义。
 */
import { describe, it, expect } from 'vitest'
import { emptyStateCopy } from './EmptyState'

describe('emptyStateCopy — kind 默认文案映射', () => {
  it('no_data：空数据提示', () => {
    const copy = emptyStateCopy('no_data')
    expect(copy.icon).toBe('📭')
    expect(copy.hint).toContain('还没有数据')
  })

  it('under_construction：建设中提示 + 引导去别处（不死白板）', () => {
    const copy = emptyStateCopy('under_construction')
    expect(copy.icon).toBe('🚧')
    expect(copy.hint).toContain('开发中')
    expect(copy.hint).toContain('别处看看')
  })

  it('两层形态与 ForbiddenCard（权限层）互不重叠：空态体系共三层', () => {
    // 契约说明性断言：EmptyState 覆盖 no_data / under_construction，
    // 权限不足层由 ForbiddenCard 承担（P0-①）。防止有人往 EmptyState
    // 塞 403 形态造成职责重叠。
    expect(emptyStateCopy('no_data').hint).not.toContain('权限')
    expect(emptyStateCopy('under_construction').hint).not.toContain('权限')
  })
})
