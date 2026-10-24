'use client'

import { useEffect, useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { evaluationService, type EvaluationV2Comparison, type EvaluationV2Experiment, type EvaluationV2Run } from '@/api/evaluation'
import { atLeast } from '@/lib/auth'
import EvaluationV2ComparisonCaseDetailButton from './EvaluationV2ComparisonCaseDetailButton'

function percent(value: number): string {
  return new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 }).format(value)
}
function pValue(value: number): string {
  return value < 0.001 ? '<0.001' : value.toFixed(3)
}
function metricValue(key: string, value: number): string {
  const ratioMetric = /rate|accuracy|faithfulness|recall|precision|mrr|ndcg/i.test(key)
  if (ratioMetric && Math.abs(value) <= 1) return percent(value)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
}
function statusText(value: string): string {
  if (value === 'pass') return '通过'
  if (value === 'fail') return '未通过'
  if (value === 'error') return '执行错误'
  if (value === 'skip') return '跳过'
  return value
}
function sliceFieldText(value: string): string {
  const labels: Record<string, string> = { category: '类别', scenario: '场景', risk_level: '风险等级', difficulty: '难度' }
  return labels[value] || value
}
function deltaTone(key: string, delta: number): string {
  if (Math.abs(delta) < 0.000001) return 'text-text-secondary'
  const lowerIsBetter = /latency|duration|cost|token/i.test(key)
  const better = lowerIsBetter ? delta < 0 : delta > 0
  return better ? 'text-emerald-700' : 'text-red-700'
}

export default function EvaluationRunComparisonPanel() {
  const [runs, setRuns] = useState<EvaluationV2Run[]>([])
  const [experiments, setExperiments] = useState<EvaluationV2Experiment[]>([])
  const [experimentId, setExperimentId] = useState('')
  const [experimentName, setExperimentName] = useState('RAG 评测实验')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [sliceField, setSliceField] = useState('all')
  const [result, setResult] = useState<EvaluationV2Comparison | null>(null)
  const [loading, setLoading] = useState(true)
  const [comparing, setComparing] = useState(false)
  const [savingExperiment, setSavingExperiment] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const load = async () => {
    if (!atLeast('admin')) { setLoading(false); return }
    setLoading(true); setError('')
    try {
      const [items, experimentItems] = await Promise.all([
        evaluationService.listV2Runs(100),
        evaluationService.listV2Experiments(100),
      ])
      setRuns(items)
      setExperiments(experimentItems)
      setBaselineId(current => current && items.some(item => item.run_id === current) ? current : items[1]?.run_id || items[0]?.run_id || '')
      setCandidateId(current => current && items.some(item => item.run_id === current) ? current : items[0]?.run_id || '')
      setResult(null)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取 V2 运行记录失败') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])

  const options = useMemo(() => runs.map(run => ({
    ...run,
    label: `${run.suite_name || run.suite_key || 'RAG Suite'} · ${run.run_id} · ${run.job_status}`,
  })), [runs])
  useEffect(() => {
    if (result && sliceField !== 'all' && !result.slices.some(item => item.field === sliceField)) {
      setSliceField('all')
    }
  }, [result, sliceField])
  const compare = async () => {
    setComparing(true); setError(''); setResult(null)
    try {
      if (!baselineId || !candidateId) throw new Error('请选择两次 V2 运行。')
      if (baselineId === candidateId) throw new Error('基线和候选不能是同一次运行。')
      setResult(await evaluationService.compareV2Runs(baselineId, candidateId))
    } catch (cause) { setError(cause instanceof Error ? cause.message : '比较 V2 运行失败') }
    finally { setComparing(false) }
  }
  const saveExperiment = async () => {
    setSavingExperiment(true); setError(''); setMessage('')
    try {
      const baseline = runs.find(run => run.run_id === baselineId)
      const candidate = runs.find(run => run.run_id === candidateId)
      if (!baseline || !candidate) throw new Error('请选择基线与候选运行。')
      if (baseline.suite_version_id !== candidate.suite_version_id) throw new Error('同一个实验只能关联相同 Suite 版本的运行。')
      const created = await evaluationService.createV2Experiment({
        suite_version_id: baseline.suite_version_id,
        name: experimentName.trim(),
      })
      await evaluationService.attachV2ExperimentRun(created.experiment_id, { run_id: baseline.run_id, slot: 'baseline' })
      const completed = await evaluationService.attachV2ExperimentRun(created.experiment_id, { run_id: candidate.run_id, slot: 'candidate' })
      setExperiments(current => [completed, ...current.filter(item => item.experiment_id !== completed.experiment_id)])
      setExperimentId(completed.experiment_id)
      setMessage(`实验已保存并绑定两次运行：${completed.name}。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存实验失败') }
    finally { setSavingExperiment(false) }
  }
  const selectExperiment = (selectedId: string) => {
    setExperimentId(selectedId)
    const selected = experiments.find(item => item.experiment_id === selectedId)
    if (!selected) return
    setBaselineId(selected.baseline_run_id || '')
    setCandidateId(selected.candidate_run_id || '')
    setResult(null)
    setMessage('已载入实验槽位，可继续比较已保存的运行。')
  }
  const baselinePass = result?.cases.filter(item => item.baseline === 'pass').length || 0
  const candidatePass = result?.cases.filter(item => item.candidate === 'pass').length || 0
  const baselineRate = result?.cases.length ? baselinePass / result.cases.length : 0
  const candidateRate = result?.cases.length ? candidatePass / result.cases.length : 0
  const delta = candidateRate - baselineRate
  const candidateOnlyPass = result?.cases.filter(item => item.baseline !== 'pass' && item.candidate === 'pass').length || 0
  const baselineOnlyPass = result?.cases.filter(item => item.baseline === 'pass' && item.candidate !== 'pass').length || 0
  const bothPass = result?.cases.filter(item => item.baseline === 'pass' && item.candidate === 'pass').length || 0
  const bothNotPass = result?.cases.filter(item => item.baseline !== 'pass' && item.candidate !== 'pass').length || 0
  const sliceFields = useMemo(() => Array.from(new Set((result?.slices || []).map(item => item.field))).sort(), [result])
  const visibleSlices = useMemo(() => (result?.slices || []).filter(item => sliceField === 'all' || item.field === sliceField), [result, sliceField])
  const passRateInference = result?.pass_rate_inference
  const selectedBaseline = runs.find(run => run.run_id === baselineId)
  const selectedCandidate = runs.find(run => run.run_id === candidateId)
  const canSaveExperiment = Boolean(selectedBaseline && selectedCandidate && selectedBaseline.suite_version_id === selectedCandidate.suite_version_id && experimentName.trim())
  const canViewRunDetails = atLeast('super_admin')

  if (!atLeast('admin')) return <p className="p-5 text-sm text-text-secondary">实验运行和对比需要评测管理员权限。</p>
  return <div className="mx-auto max-w-7xl space-y-5 px-6 py-6">
    <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-compare-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="evaluation-compare-heading" className="text-base font-semibold text-text-primary">V2 实验 A/B 对比</h2><p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">对比请求由服务端校验 Suite、Golden、语料和逐例覆盖。快照不兼容时只返回原因，不生成提升或回退结论。</p></div>
        <button type="button" onClick={() => void load()} disabled={loading || comparing} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-border-subtle px-3 text-sm text-text-primary hover:bg-bg-hover disabled:opacity-50"><RefreshCw size={15} />刷新运行</button>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="text-sm font-medium text-text-primary">基线 A<select aria-label="基线 A" value={baselineId} onChange={event => { setBaselineId(event.target.value); setResult(null) }} className="mt-2 min-h-11 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">选择运行</option>{options.map(item => <option key={item.run_id} value={item.run_id}>{item.label}</option>)}</select></label>
        <label className="text-sm font-medium text-text-primary">候选 B<select aria-label="候选 B" value={candidateId} onChange={event => { setCandidateId(event.target.value); setResult(null) }} className="mt-2 min-h-11 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">选择运行</option>{options.map(item => <option key={item.run_id} value={item.run_id}>{item.label}</option>)}</select></label>
      </div>
      <button type="button" onClick={() => void compare()} disabled={loading || comparing || !baselineId || !candidateId} className="mt-4 min-h-11 rounded-md bg-accent px-4 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">{comparing ? '校验快照并比较…' : '比较实验'}</button>
      <div className="mt-4 grid gap-3 border-t border-border-subtle pt-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <label className="text-sm font-medium text-text-primary">已保存实验<select aria-label="已保存实验" value={experimentId} onChange={event => selectExperiment(event.target.value)} className="mt-2 min-h-11 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">选择已保存实验</option>{experiments.map(item => <option key={item.experiment_id} value={item.experiment_id}>{item.name} · {item.experiment_id.slice(0, 8)}</option>)}</select></label>
        <label className="text-sm font-medium text-text-primary">新实验名称<input aria-label="新实验名称" value={experimentName} onChange={event => setExperimentName(event.target.value)} maxLength={160} className="mt-2 min-h-11 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm" /></label>
        <button type="button" onClick={() => void saveExperiment()} disabled={loading || savingExperiment || !canSaveExperiment} className="mt-6 min-h-11 rounded-md border border-border-subtle px-4 text-sm font-medium text-text-primary hover:bg-bg-hover disabled:opacity-50">{savingExperiment ? '保存中…' : '保存为实验'}</button>
      </div>
      {loading && <p className="mt-3 text-sm text-text-secondary" role="status">正在读取 V2 运行记录…</p>}
      {runs.length === 0 && !loading && <p className="mt-3 rounded-md bg-bg-elevated p-4 text-sm text-text-secondary">没有 V2 运行记录。请先启动已发布 RAG Suite。</p>}
      {error && <p className="mt-3 rounded-md border border-red-200 p-3 text-sm text-red-800" role="alert">{error}</p>}
      {message && <p className="mt-3 text-sm text-text-secondary" role="status">{message}</p>}
    </section>
    {result && !result.comparable && <section className="rounded-lg border border-amber-300 bg-amber-50 p-4" aria-labelledby="evaluation-incomparable-heading"><h3 id="evaluation-incomparable-heading" className="font-semibold text-amber-950">不可直接比较</h3><p className="mt-1 text-sm text-amber-900">关键运行快照不一致或结果未完成，系统没有计算差值：</p><ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-900">{result.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></section>}
    {result?.comparable && <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">通过率差值 · B − A</p><p className={'mt-2 text-2xl font-semibold tabular-nums ' + deltaTone('pass_rate', delta)}>{delta > 0 ? '+' : ''}{percent(delta)}</p><p className="mt-1 text-xs text-text-muted">{percent(baselineRate)} → {percent(candidateRate)}</p></article>
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">可比较样本</p><p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{result.cases.length}</p><p className="mt-1 text-xs text-text-muted">相同 Case ID 与修订</p></article>
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">仅 B 通过</p><p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{candidateOnlyPass}</p></article>
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">仅 A 通过</p><p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{baselineOnlyPass}</p></article>
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">双方通过</p><p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{bothPass}</p></article>
        <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">双方未通过 / 错误</p><p className="mt-2 text-2xl font-semibold tabular-nums text-text-primary">{bothNotPass}</p></article>
      </div>
      {passRateInference && <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-compare-inference"><div className="flex flex-wrap items-start justify-between gap-2"><div><h3 id="evaluation-compare-inference" className="font-semibold text-text-primary">配对通过率不确定性</h3><p className="mt-1 text-xs text-text-muted">双侧精确 McNemar 检验；95% 区间使用配对 Wilson 近似。该检验只针对整体二分类结果。</p></div><span className="rounded-full bg-bg-elevated px-2.5 py-1 text-xs text-text-secondary">有效样本 {passRateInference.sample_count} · 状态不完整排除 {passRateInference.excluded_count}</span></div><div className="mt-3 grid gap-3 sm:grid-cols-3"><div><p className="text-xs text-text-muted">通过率差值 95% 区间</p><p className="mt-1 text-sm font-medium text-text-primary">{passRateInference.ci95_lower === null || passRateInference.ci95_upper === null ? '无方向变化样本，无法估计' : `${percent(passRateInference.ci95_lower)} 至 ${percent(passRateInference.ci95_upper)}`}</p></div><div><p className="text-xs text-text-muted">双侧精确 p 值</p><p className="mt-1 text-sm font-medium tabular-nums text-text-primary">{passRateInference.p_value < 0.001 ? '< 0.001' : passRateInference.p_value.toFixed(3)}</p></div><div><p className="text-xs text-text-muted">不一致样本方向</p><p className="mt-1 text-sm text-text-primary">B 新通过 {passRateInference.candidate_only_pass} · A 通过而 B 未通过 {passRateInference.baseline_only_pass}</p></div></div><p className="mt-3 text-xs text-text-muted">下方切片仅对有效样本至少 20 的标签组计算配对推断，并对可检验组统一做 Holm 校正；结果仅供探索，不参与 Gate。</p></section>}
      <section className="overflow-x-auto rounded-lg border border-border-subtle bg-surface-base" aria-labelledby="evaluation-compare-metrics"><div className="border-b border-border-subtle p-4"><h3 id="evaluation-compare-metrics" className="font-semibold text-text-primary">共同返回的质量指标</h3><p className="mt-1 text-xs text-text-muted">按逐例已持久化指标求均值；旧 Runner 未记录的成本不填为零。</p></div>{result.metrics.length === 0 ? <p className="p-4 text-sm text-text-secondary">两次运行没有共同数值指标。</p> : <table className="w-full min-w-[620px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">指标</th><th className="px-4 py-3">基线 A</th><th className="px-4 py-3">候选 B</th><th className="px-4 py-3">变化 B − A</th><th className="px-4 py-3">样本量</th></tr></thead><tbody className="divide-y divide-border-subtle">{result.metrics.map(metric => <tr key={metric.key}><td className="px-4 py-3">{metric.key}</td><td className="px-4 py-3 tabular-nums">{metricValue(metric.key, metric.baseline)}</td><td className="px-4 py-3 tabular-nums">{metricValue(metric.key, metric.candidate)}</td><td className={'px-4 py-3 tabular-nums font-medium ' + deltaTone(metric.key, metric.delta)}>{metric.delta > 0 ? '+' : ''}{metricValue(metric.key, metric.delta)}</td><td className="px-4 py-3 tabular-nums">{metric.sample_count}</td></tr>)}</tbody></table>}</section>
      <section className="overflow-x-auto rounded-lg border border-border-subtle bg-surface-base" aria-labelledby="evaluation-compare-slices">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle p-4">
          <div><h3 id="evaluation-compare-slices" className="font-semibold text-text-primary">切片对比</h3><p className="mt-1 text-xs text-text-muted">标签和逐题状态来自 Run 快照。有效样本量至少 20 时，另显示 pass/fail 配对 McNemar 检验与 Holm 校正；结果仅供探索，不参与 Gate。</p></div>
          <label className="text-xs text-text-secondary">切片维度<select aria-label="切片维度" value={sliceField} onChange={event => setSliceField(event.target.value)} className="ml-2 min-h-9 rounded-md border border-border-subtle bg-surface-base px-2 text-sm"><option value="all">全部维度</option>{sliceFields.map(field => <option key={field} value={field}>{sliceFieldText(field)}</option>)}</select></label>
        </div>
        {result.slice_warning && <p role="status" className="border-b border-border-subtle px-4 py-3 text-xs text-amber-800">{result.slice_warning}</p>}
        {visibleSlices.length === 0 ? <p className="p-4 text-sm text-text-secondary">样本没有可用的类别、场景、风险或难度标签。</p> : <table className="w-full min-w-[920px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">维度</th><th className="px-4 py-3">分组</th><th className="px-4 py-3">样本量</th><th className="px-4 py-3">A 通过率</th><th className="px-4 py-3">B 通过率</th><th className="px-4 py-3">变化 B − A</th><th className="px-4 py-3">配对推断（探索性）</th><th className="px-4 py-3">评分指标变化</th></tr></thead><tbody className="divide-y divide-border-subtle">{visibleSlices.map(item => { const inference = item.pass_rate_inference; return <tr key={`${item.field}:${item.value}`}><td className="px-4 py-3">{sliceFieldText(item.field)}</td><td className="px-4 py-3">{item.value}</td><td className="px-4 py-3 tabular-nums">{item.sample_count}</td><td className="px-4 py-3 tabular-nums">{percent(item.baseline_pass_rate)}</td><td className="px-4 py-3 tabular-nums">{percent(item.candidate_pass_rate)}</td><td className={'px-4 py-3 tabular-nums font-medium ' + deltaTone('pass_rate', item.pass_rate_delta)}>{item.pass_rate_delta > 0 ? '+' : ''}{percent(item.pass_rate_delta)}</td><td className="px-4 py-3 text-xs text-text-secondary">{!inference ? '未提供' : !inference.eligible ? `有效样本 n=${inference.sample_count}，少于 ${inference.minimum_sample_count}，不做推断` : <><p>有效样本 n={inference.sample_count} · Holm 校正 p={pValue(inference.adjusted_p_value ?? 1)}</p>{inference.ci95_lower != null && inference.ci95_upper != null ? <p>95% CI [{percent(inference.ci95_lower)}, {percent(inference.ci95_upper)}]</p> : <p>没有 pass/fail 状态变化</p>}</>}</td><td className="px-4 py-3 text-xs">{item.metrics.length ? item.metrics.map(metric => <p key={metric.key} className={deltaTone(metric.key, metric.delta)}>{metric.key}: {metric.delta > 0 ? '+' : ''}{metricValue(metric.key, metric.delta)} · n={metric.sample_count}</p>) : '—'}</td></tr> })}</tbody></table>}
        {result.slices_truncated > 0 && <p className="border-t border-border-subtle px-4 py-3 text-xs text-text-muted">标签分组较多，仅显示样本量最大的 200 组，另有 {result.slices_truncated} 组未展开。</p>}
      </section>
      <section className="overflow-x-auto rounded-lg border border-border-subtle bg-surface-base" aria-labelledby="evaluation-compare-cases"><div className="border-b border-border-subtle p-4"><h3 id="evaluation-compare-cases" className="font-semibold text-text-primary">逐题变化</h3><p className="mt-1 text-xs text-text-muted">Case 状态来自已保存的 V2 逐例结果；超级管理员可查看服务端脱敏后的回答差异。</p></div><table className="w-full min-w-[760px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">Case</th><th className="px-4 py-3">基线 A</th><th className="px-4 py-3">候选 B</th><th className="px-4 py-3">变化</th><th className="px-4 py-3">答案 diff</th></tr></thead><tbody className="divide-y divide-border-subtle">{result.cases.map(item => <tr key={item.case_id}><td className="px-4 py-3 font-mono text-xs">{item.case_id}</td><td className="px-4 py-3">{statusText(item.baseline)}</td><td className="px-4 py-3">{statusText(item.candidate)}</td><td className="px-4 py-3">{item.baseline === item.candidate ? '一致' : item.baseline !== 'pass' && item.candidate === 'pass' ? 'B 新通过' : item.baseline === 'pass' && item.candidate !== 'pass' ? 'B 回退' : '状态变化'}</td><td className="px-4 py-3">{canViewRunDetails && <EvaluationV2ComparisonCaseDetailButton baselineRunId={baselineId} candidateRunId={candidateId} caseId={item.case_id} />}</td></tr>)}</tbody></table></section>
    </>}
  </div>
}
