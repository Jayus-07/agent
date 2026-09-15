/** UploadDialog 辅助函数测试 — fmtMs 耗时格式化 */
import { describe, it, expect } from 'vitest'

/** Mirror of UploadDialog.fmtMs — 验证耗时格式化逻辑 */
function fmtMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '<1ms'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

describe('fmtMs — 耗时格式化', () => {
  it('<1ms 时返回 <1ms', () => {
    expect(fmtMs(0)).toBe('<1ms')
    expect(fmtMs(0.5)).toBe('<1ms')
    expect(fmtMs(-1)).toBe('<1ms')
  })

  it('毫秒级显示 ms', () => {
    expect(fmtMs(1)).toBe('1ms')
    expect(fmtMs(500)).toBe('500ms')
    expect(fmtMs(999)).toBe('999ms')
  })

  it('秒级显示 x.xs', () => {
    expect(fmtMs(1000)).toBe('1.0s')
    expect(fmtMs(1500)).toBe('1.5s')
    expect(fmtMs(60000)).toBe('60.0s')
  })

  it('NaN/Infinity 安全', () => {
    expect(fmtMs(NaN)).toBe('<1ms')
    expect(fmtMs(Infinity)).toBe('<1ms')
  })
})
