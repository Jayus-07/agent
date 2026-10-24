import { describe, expect, it } from 'vitest'
import type { EvalRunDetail, RunSummary } from '@/api/evaluation'
import { compareEvaluationRuns } from './evaluationComparison'

function run(id: string): RunSummary {
  return { run_id: id, module: 'rag', pass_rate: 0.5, top1_accuracy: 0, faithfulness: 0, answer_correctness: 0, recall_at_5: 0, reject_accuracy: 0, mrr: 0, ndcg_at_10: 0, timestamp: '', status: 'completed', validity: 'VALID', suite: 'core', dataset_version: '1.0' }
}
function detail(id: string, datasetHash = 'hash-a', p95Latency = 30): EvalRunDetail {
  return {
    run_id: id, run_status: { status: 'completed' }, meta: {},
    report: {
      timestamp: '', module: 'rag', mode: 'offline', smoke: false, tier: '',
      summaries: [{ module: 'rag', total: 1, passed: 1, failed: 0, errors: 0, skipped: 0, pass_rate: 1, metrics: { faithfulness: 0.9, p95_latency_ms: p95Latency } }],
      tier_summaries: [],
      results: [{ case_id: 'case-1', module: 'rag', status: 'pass', expected: {}, actual: {}, metrics: {}, duration_ms: 1, error_msg: null }],
      metadata: {
        run_validity: { validity: 'VALID' },
        eval_provenance: { suite: 'core', dataset_version: '1.0', dataset_content_hash: datasetHash, corpus_snapshot_hash: 'corpus-a' },
        evaluation_snapshot_inputs: { suite: { name: 'core', dataset_version: '1.0', suite_content_hash: datasetHash }, corpus_snapshot_content_hash: 'corpus-a', judge_config: { version: 'j1' }, threshold_snapshot: { pass: 0.8 } },
      },
    },
  }
}
describe('评测运行比较', () => {
  it('相同数据、语料、评分规则和样本时比较服务端真实指标与逐例状态', () => {
    const actual = compareEvaluationRuns(run('a'), detail('a', 'hash-a', 30), run('b'), detail('b', 'hash-a', 20))
    expect(actual.comparable).toBe(true)
    expect(actual.sampleCount).toBe(1)
    expect(actual.caseCounts.bothPass).toBe(1)
    expect(actual.metrics.find(metric => metric.key === 'p95_latency_ms')?.delta).toBe(-10)
    expect(actual.metrics.some(metric => metric.key === 'faithfulness')).toBe(true)
  })
  it('数据版本哈希不同时拒绝输出差值', () => {
    const actual = compareEvaluationRuns(run('a'), detail('a'), run('b'), detail('b', 'hash-b'))
    expect(actual.comparable).toBe(false)
    expect(actual.reasons.join(' ')).toContain('内容哈希不同')
    expect(actual.caseCounts.candidateOnlyPass).toBe(0)
  })
  it('无效运行不能用于质量对比', () => {
    const invalid = detail('a')
    invalid.report.metadata = { ...invalid.report.metadata, run_validity: { validity: 'INVALID_PROVIDER' } }
    const actual = compareEvaluationRuns(run('a'), invalid, run('b'), detail('b'))
    expect(actual.comparable).toBe(false)
    expect(actual.reasons.join(' ')).toContain('有效运行')
  })
})
