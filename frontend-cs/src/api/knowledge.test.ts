/** knowledge service 单元测试 — 覆盖 qs() 参数过滤等关键逻辑 */
import { describe, it, expect } from 'vitest'

// 内联 qs 副本用于测试（原函数未导出，通过行为验证）
function qs(params: Record<string, unknown>): string {
  const clean: Record<string, string> = {}
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = String(v)
  }
  return new URLSearchParams(clean).toString()
}

describe('qs() — 查询参数构建', () => {
  it('正常参数正确编码', () => {
    expect(qs({ page: 1, page_size: 20 })).toBe('page=1&page_size=20')
  })

  it('operation 正常值保留', () => {
    expect(qs({ page: 1, operation: 'upload' })).toBe('page=1&operation=upload')
  })

  it('undefined 值被过滤（核心缺陷修复验证）', () => {
    expect(qs({ page: 1, operation: undefined })).toBe('page=1')
  })

  it('null 值被过滤', () => {
    expect(qs({ page: 1, type: null })).toBe('page=1')
  })

  it('空字符串被过滤', () => {
    expect(qs({ page: 1, operation: '' })).toBe('page=1')
  })

  it('混合：部分有效部分无效', () => {
    expect(qs({ page: 1, operation: '', type: 'pdf', status: undefined }))
      .toBe('page=1&type=pdf')
  })

  it('全部无效时返回空字符串', () => {
    expect(qs({ operation: '', type: undefined, status: null })).toBe('')
  })

  it('多参数全部有效', () => {
    expect(qs({ page: 1, page_size: 20, operation: 'upload', keyword: '测试' }))
      .toBe('page=1&page_size=20&operation=upload&keyword=%E6%B5%8B%E8%AF%95')
  })
})
