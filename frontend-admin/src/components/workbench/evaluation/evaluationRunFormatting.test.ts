import { describe, expect, it } from 'vitest'
import { formatEvaluationRunCost } from './evaluationRunFormatting'

describe('formatEvaluationRunCost', () => {
  it('没有账单归因时显示未归因，不显示 0', () => {
    expect(formatEvaluationRunCost(null, null)).toBe('未归因')
    expect(formatEvaluationRunCost(undefined, undefined)).toBe('未归因')
  })

  it('保留真实零费用和币种', () => {
    expect(formatEvaluationRunCost(0, 'USD')).toBe('0 USD')
    expect(formatEvaluationRunCost('0.12500000', 'CNY')).toBe('0.12500000 CNY')
  })
})
