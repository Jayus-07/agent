import { describe, expect, it } from 'vitest'
import {
  RAG_LOW_CONFIDENCE_THRESHOLD,
  formatSourcePages,
  isLowConfidence,
  toRejectionActionKey,
} from './ragAnswer'

describe('ragAnswer 语义归一', () => {
  it('后端蛇形码 → ErrorCard 行动指引键（大写）', () => {
    expect(toRejectionActionKey('rag_no_evidence')).toBe('RAG_NO_EVIDENCE')
    expect(toRejectionActionKey('rag_permission_denied')).toBe('RAG_PERMISSION_DENIED')
    expect(toRejectionActionKey('rag_hallucination')).toBe('RAG_HALLUCINATION')
  })

  it('低置信判定：<0.6 为真，≥0.6 或缺失为假', () => {
    expect(isLowConfidence(0.42)).toBe(true)
    expect(isLowConfidence(0.59)).toBe(true)
    expect(isLowConfidence(RAG_LOW_CONFIDENCE_THRESHOLD)).toBe(false)
    expect(isLowConfidence(0.85)).toBe(false)
    expect(isLowConfidence(undefined)).toBe(false)
    expect(isLowConfidence(-0.1)).toBe(false)
  })

  it('页码文案：单页 / 连续区间合并 / 离散列举 / 超三页截断', () => {
    expect(formatSourcePages(undefined)).toBe('')
    expect(formatSourcePages([])).toBe('')
    expect(formatSourcePages([3])).toBe('第 3 页')
    expect(formatSourcePages([3, 4, 5])).toBe('第 3-5 页')
    expect(formatSourcePages([4, 3])).toBe('第 3-4 页')
    expect(formatSourcePages([2, 5])).toBe('第 2、5 页')
    expect(formatSourcePages([2, 5, 9, 12])).toBe('第 2、5、9 页 等')
  })
})
