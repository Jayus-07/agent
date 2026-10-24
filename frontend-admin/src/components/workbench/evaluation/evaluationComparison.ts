import type { EvalRunDetail, RunSummary } from '@/api/evaluation'

type JsonRecord = Record<string, unknown>
function record(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']'
  if (value && typeof value === 'object') {
    const object = value as JsonRecord
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + stable(object[key])).join(',') + '}'
  }
  return JSON.stringify(value) ?? String(value)
}
function firstString(...values: unknown[]): string {
  return values.find(value => typeof value === 'string' && value.trim()) as string || ''
}

export interface ComparedCase { case_id: string; baseline: string; candidate: string }
export interface ComparedMetric { key: string; label: string; baseline: number; candidate: number; delta: number }
export interface ComparisonResult {
  comparable: boolean
  reasons: string[]
  baselinePassRate: number
  candidatePassRate: number
  caseCounts: { candidateOnlyPass: number; baselineOnlyPass: number; bothFail: number; bothPass: number }
  cases: ComparedCase[]
  metrics: ComparedMetric[]
  sampleCount: number
  uncertainty: string
}

function comparisonInputs(run: RunSummary, detail: EvalRunDetail) {
  const metadata = record(detail.report.metadata)
  const meta = record(detail.meta)
  const inputs = record(metadata.evaluation_snapshot_inputs || meta.evaluation_snapshot_inputs)
  const provenance = record(metadata.eval_provenance || meta.eval_provenance)
  const suite = record(inputs.suite)
  const governance = record(metadata.suite_governance)
  const runValidity = record(metadata.run_validity)
  return {
    status: firstString(detail.run_status?.status, run.status),
    validity: firstString(runValidity.validity, run.validity),
    suiteName: firstString(provenance.suite, suite.name, metadata.selection, run.suite),
    datasetVersion: firstString(provenance.dataset_version, suite.dataset_version, metadata.dataset_version, run.dataset_version),
    datasetHash: firstString(provenance.dataset_content_hash, suite.suite_content_hash, suite.cases_hash, governance.content_hash),
    corpusHash: firstString(inputs.corpus_snapshot_content_hash, metadata.corpus_snapshot_hash, provenance.corpus_snapshot_hash),
    corpusVersion: firstString(inputs.corpus_version_id, provenance.version_id),
    evaluator: {
      mode: firstString(metadata.evaluator_mode, run.evaluator_mode),
      judge: inputs.judge_config ?? metadata.judge_config,
      ragas: inputs.ragas_config ?? metadata.ragas_config,
      thresholds: inputs.threshold_snapshot ?? governance.thresholds,
    },
    caseIds: detail.report.results.map(item => item.case_id).filter(Boolean).sort(),
  }
}
function caseStatus(detail: EvalRunDetail, id: string): string {
  return detail.report.results.find(item => item.case_id === id)?.status || 'missing'
}
function metricValues(run: RunSummary, detail: EvalRunDetail): Record<string, number> {
  const summary = detail.report.summaries.find(item => item.module === detail.report.module) || detail.report.summaries[0]
  const raw = record(summary?.metrics)
  const out: Record<string, number> = { pass_rate: run.pass_rate }
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === 'number' && Number.isFinite(value) && !/^(total|passed|failed|errors|skipped|case_count|sample_count)$/i.test(key)) out[key] = value
  }
  return out
}
function metricLabel(key: string): string {
  const labels: Record<string, string> = {
    pass_rate: '通过率',
    top1_accuracy: 'Top-1 准确率',
    faithfulness: '忠实度',
    answer_correctness: '答案正确率',
    recall_at_5: 'Recall@5',
    reject_accuracy: '拒答准确率',
    mrr: 'MRR',
    ndcg_at_10: 'NDCG@10',
    p95_latency_ms: 'P95 延迟（ms）',
    latency_p95_ms: 'P95 延迟（ms）',
    estimated_cost: '估算费用',
    cost: '费用',
  }
  return labels[key] || key.replaceAll('_', ' ')
}
function sharedMetrics(a: Record<string, number>, b: Record<string, number>): ComparedMetric[] {
  return Object.keys(a).filter(key => typeof b[key] === 'number').sort().map(key => ({
    key,
    label: metricLabel(key),
    baseline: a[key],
    candidate: b[key],
    delta: b[key] - a[key],
  }))
}

export function compareEvaluationRuns(
  baseline: RunSummary,
  baselineDetail: EvalRunDetail,
  candidate: RunSummary,
  candidateDetail: EvalRunDetail,
): ComparisonResult {
  const reasons: string[] = []
  const a = comparisonInputs(baseline, baselineDetail)
  const b = comparisonInputs(candidate, candidateDetail)
  if (a.status !== 'completed' || b.status !== 'completed') reasons.push('两次运行都必须已完成。')
  if (a.validity !== 'VALID' || b.validity !== 'VALID') reasons.push('两次运行都必须是有效运行；环境无效不能作为质量对比。')
  if (!a.suiteName || a.suiteName !== b.suiteName) reasons.push('Suite 不同或缺少 Suite 记录。')
  if (!a.datasetVersion || a.datasetVersion !== b.datasetVersion) reasons.push('DatasetVersion 不同或缺少版本记录。')
  if (!a.datasetHash || a.datasetHash !== b.datasetHash) reasons.push('Suite / 数据集内容哈希不同或缺失。')
  if (!a.corpusHash || !b.corpusHash || a.corpusHash !== b.corpusHash) {
    if (!a.corpusVersion || !b.corpusVersion || a.corpusVersion !== b.corpusVersion) reasons.push('CorpusSnapshot 内容哈希不同或缺少可验证的快照版本。')
  }
  if (stable(a.evaluator) !== stable(b.evaluator)) reasons.push('评判器模式、评判配置或阈值快照不同。')
  if (!a.caseIds.length || stable(a.caseIds) !== stable(b.caseIds)) reasons.push('两次运行的实际样本集合不同或没有逐例记录。')

  const ids = Array.from(new Set([...a.caseIds, ...b.caseIds])).sort()
  const cases = ids.map(case_id => ({ case_id, baseline: caseStatus(baselineDetail, case_id), candidate: caseStatus(candidateDetail, case_id) }))
  const caseCounts = { candidateOnlyPass: 0, baselineOnlyPass: 0, bothFail: 0, bothPass: 0 }
  if (!reasons.length) for (const item of cases) {
    if (item.baseline === 'pass' && item.candidate === 'pass') caseCounts.bothPass += 1
    else if (item.baseline === 'pass') caseCounts.baselineOnlyPass += 1
    else if (item.candidate === 'pass') caseCounts.candidateOnlyPass += 1
    else caseCounts.bothFail += 1
  }
  const sampleCount = Math.min(a.caseIds.length, b.caseIds.length)
  return {
    comparable: reasons.length === 0,
    reasons,
    baselinePassRate: baseline.pass_rate,
    candidatePassRate: candidate.pass_rate,
    caseCounts,
    cases,
    metrics: sharedMetrics(metricValues(baseline, baselineDetail), metricValues(candidate, candidateDetail)),
    sampleCount,
    uncertainty: sampleCount < 30 ? '样本量少于 30 条，差值波动较大，应结合逐题证据判断。' : '通过率为观测值，未计算置信区间。',
  }
}
