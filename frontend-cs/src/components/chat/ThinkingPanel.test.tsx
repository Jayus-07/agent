/** ThinkingPanel 标题文案回归测试 — 思考中 → 已思考（用时 N 秒）状态机 */
import { describe, it, expect } from 'vitest'
import { thinkingLabel } from './ThinkingPanel'

describe('thinkingLabel — 面板标题文案', () => {
  it('流式进行中且回答未开始 → 思考中…', () => {
    expect(thinkingLabel(true, false, null)).toBe('思考中…')
  })

  it('回答开始后 → 已思考（用时 N 秒）', () => {
    expect(thinkingLabel(true, true, 10)).toBe('已思考（用时 10 秒）')
  })

  it('思考被中止（无耗时）→ 已思考', () => {
    expect(thinkingLabel(true, true, null)).toBe('已思考')
    expect(thinkingLabel(false, false, null)).toBe('已思考')
  })

  it('历史消息（非 live）带耗时 → 已思考（用时 N 秒）', () => {
    expect(thinkingLabel(false, false, 3)).toBe('已思考（用时 3 秒）')
  })
})
