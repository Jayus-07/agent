import { describe, expect, it } from 'vitest'
import {
  RAG_CORE_METRICS,
  RAGAS_OPTIONAL_METRICS,
  ALL_EVAL_METRICS,
  resolveMetricDef,
  getScoreLevel,
  formatScorePercent,
  getScoreTone,
} from './ragMetrics'

describe('ragMetrics', () => {
  it('包含完整的 5 维黄金指标与联合 Ragas 可选评测指标', () => {
    expect(RAG_CORE_METRICS).toHaveLength(5)
    expect(RAG_CORE_METRICS.map(m => m.key)).toEqual([
      'context_recall',
      'context_precision',
      'faithfulness',
      'answer_relevance',
      'answer_correctness',
    ])
    expect(RAGAS_OPTIONAL_METRICS.length).toBeGreaterThanOrEqual(4)
    expect(ALL_EVAL_METRICS.length).toBe(RAG_CORE_METRICS.length + RAGAS_OPTIONAL_METRICS.length)
  })

  it('能正确解析带或不带 ragas_ 前缀的 metric key', () => {
    const direct = resolveMetricDef('context_recall')
    expect(direct?.label).toBe('上下文召回率')
    expect(direct?.category).toBe('retrieval')

    const prefixed = resolveMetricDef('ragas_faithfulness')
    expect(prefixed?.label).toBe('忠实度 / 无幻觉率')
    expect(prefixed?.category).toBe('generation')

    const optional = resolveMetricDef('ragas_context_entity_recall')
    expect(optional?.isRagasOptional).toBe(true)
    expect(optional?.label).toBe('上下文实体召回率')

    const unknown = resolveMetricDef('non_existent_key')
    expect(unknown).toBeUndefined()
  })

  it('能准确计算评级、百分比格式化与 Tone 语义', () => {
    expect(getScoreLevel(0.95)).toBe('excellent')
    expect(getScoreLevel(0.85, 0.8)).toBe('good')
    expect(getScoreLevel(0.72, 0.8)).toBe('warning')
    expect(getScoreLevel(0.45, 0.8)).toBe('poor')
    expect(getScoreLevel(null)).toBe('na')
    expect(getScoreLevel(undefined)).toBe('na')

    expect(formatScorePercent(0.925)).toBe('92.5%')
    expect(formatScorePercent(null)).toBe('—')

    expect(getScoreTone('excellent')).toBe('good')
    expect(getScoreTone('warning')).toBe('warn')
    expect(getScoreTone('poor')).toBe('bad')
    expect(getScoreTone('na')).toBe('muted')
  })
})
