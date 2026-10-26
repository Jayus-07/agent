'use client'

/**
 * 结果对比 tab —— 严格按已定稿原型实现：
 * 数据集筛选 → 基准/对比两卡 → 指标分组表（自研 / RAGAS / 成本时延）→ 逐用例表 → 用例详情（含 Trace 跳转）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import {
  evaluationService,
  type EvaluationV2Comparison,
  type EvaluationV2Run,
  type EvaluationV2RunCaseDetail,
} from '@/api/evaluation'
import { atLeast } from '@/lib/auth'
import { Badge, Card, ctl, ctlGhost, Empty, modeShort, TableWrap, Td, Th, timeShort } from './evalUi'

const GROUP_LABELS: Array<{ label: string; match: (key: string) => boolean }> = [
  { label: 'RAGAS 指标（LLM Judge 评分）', match: key => key.startsWith('ragas_') },
  { label: '成本与时延', match: key => /latency|token|cost|duration/i.test(key) },
  { label: '自研指标', match: () => true },
]
const METRIC_LABELS: Record<string, string> = {
  ragas_faithfulness: '忠实度 Faithfulness',
  ragas_answer_relevancy: '答案相关性 Answer Relevancy',
  ragas_context_precision: '上下文精确率 Context Precision',
  ragas_context_recall: '上下文召回率 Context Recall',
  pass_rate: '通过率',
  doc_mrr: 'MRR',
  top1_accuracy: 'Top-1 准确率',
}

/** 逐题主分数：不同 Runner 的分数键不统一，按优先级取第一个数值分 */
const PRIMARY_SCORE_KEYS = ['overall', 'score', 'pass_score', 'judge_score', 'total_score']
function primaryScore(scores: Record<string, number | null> | undefined): number | null {
  if (!scores) return null
  for (const key of PRIMARY_SCORE_KEYS) {
    const value = scores[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

function formatScore(value: number | null): string {
  return value === null ? '—' : value.toFixed(2)
}

export default function CompareTab({ focusRunId }: { focusRunId?: string | null }) {
  const [runs, setRuns] = useState<EvaluationV2Run[]>([])
  const [suiteFilter, setSuiteFilter] = useState('all')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [comparison, setComparison] = useState<EvaluationV2Comparison | null>(null)
  const [caseDetail, setCaseDetail] = useState<EvaluationV2RunCaseDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!atLeast('super_admin')) { setLoading(false); return }
    setLoading(true); setError('')
    try { setRuns(await evaluationService.listV2Runs(100)) }
    catch (e) { setError(e instanceof Error ? e.message : '读取运行失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!focusRunId) return
    setCandidateId(focusRunId)
  }, [focusRunId])

  const completed = useMemo(() => runs.filter(run => run.job_status === 'completed'), [runs])
  const suiteNames = useMemo(() => Array.from(new Set(completed.map(run => run.suite_name || run.suite_key || ''))).filter(Boolean), [completed])
  const filtered = useMemo(() => completed.filter(run => suiteFilter === 'all' || (run.suite_name || run.suite_key) === suiteFilter), [completed, suiteFilter])

  const compare = () => {
    if (!baselineId || !candidateId || baselineId === candidateId) return
    setBusy(true); setError(''); setComparison(null); setCaseDetail(null)
    evaluationService.compareV2Runs(baselineId, candidateId)
      .then(setComparison)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '对比失败'))
      .finally(() => setBusy(false))
  }

  const grouped = useMemo(() => {
    if (!comparison) return []
    return GROUP_LABELS.map(group => ({
      label: group.label,
      metrics: comparison.metrics.filter(metric => group.match(metric.key)),
    })).filter(group => group.metrics.length > 0)
  }, [comparison])

  const openCase = (caseId: string) => {
    if (!candidateId) return
    void evaluationService.getV2RunCaseDetail(candidateId, caseId).then(setCaseDetail).catch(() => setCaseDetail(null))
  }

  if (!atLeast('super_admin')) return <p className="p-5 text-sm text-text-secondary">结果对比沿用任务授权，仅超级管理员可查看。</p>

  return <div className="space-y-4">
    {error && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}

    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-2 text-xs text-text-secondary">数据集
        <select aria-label="切换对比数据集" value={suiteFilter} onChange={e => { setSuiteFilter(e.target.value); setBaselineId(''); setCandidateId(''); setComparison(null) }} className={ctl}>
          <option value="all">全部</option>
          {suiteNames.map(name => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
    </div>

    <div className="grid gap-3 sm:grid-cols-2">
      {[{ label: '基准', value: baselineId, set: setBaselineId }, { label: '对比', value: candidateId, set: setCandidateId }].map(slot => {
        const run = filtered.find(item => item.run_id === slot.value)
        return <div key={slot.label} className={`rounded-lg border bg-surface-base p-3.5 ${slot.label === '对比' && slot.value ? 'border-accent' : 'border-border-subtle'}`}>
          <Badge text={slot.label} tone={slot.label === '对比' ? 'accent' : 'gray'} />
          <select aria-label={`${slot.label}运行`} value={slot.value} onChange={e => slot.set(e.target.value)} className={`${ctl} mt-2 w-full`}>
            <option value="">选择运行</option>
            {filtered.map(item => <option key={item.run_id} value={item.run_id}>{item.run_id.slice(0, 8)} · {item.suite_name} · {timeShort(item.finished_at)}</option>)}
          </select>
          {run && <p className="mt-2 text-xs text-text-muted">{run.suite_name} · {modeShort(run.evaluation_mode)} · {timeShort(run.finished_at)} · 通过 {run.passed_cases}/{run.total_cases}</p>}
        </div>
      })}
    </div>
    <div className="flex justify-end">
      <button type="button" className={ctlGhost} disabled={busy || !baselineId || !candidateId || baselineId === candidateId} onClick={compare}>{busy ? '对比中…' : '开始对比'}</button>
    </div>

    {comparison && !comparison.comparable && <Card title="口径校验未通过" subtitle="两次运行不可直接对比的原因：">
      <ul className="list-disc pl-5 text-xs text-text-secondary">{comparison.reasons.map((reason, i) => <li key={i}>{typeof reason === 'string' ? reason : JSON.stringify(reason)}</li>)}</ul>
    </Card>}

    {comparison?.comparable && <>
      <Card title="指标对比" subtitle="错误与跳过的用例不参与质量分；口径不同会先行拒绝。">
        {grouped.length === 0 ? <Empty text="本次对比没有可展示的数值指标。" /> : grouped.map(group => <div key={group.label} className="mb-4 last:mb-0">
          <p className="mb-1.5 text-xs font-medium text-text-muted">{group.label}</p>
          <TableWrap minWidth={560}>
            <thead className="bg-bg-elevated"><tr><Th>指标</Th><Th end>基准</Th><Th end>对比</Th><Th end>变化</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {group.metrics.map(metric => {
                const good = metric.delta > 0
                return <tr key={metric.key}>
                  <Td><span className="text-text-primary">{METRIC_LABELS[metric.key] || metric.key}</span></Td>
                  <Td end>{metric.baseline.toFixed(4)}</Td>
                  <Td end>{metric.candidate.toFixed(4)}</Td>
                  <Td end><span className={`font-medium ${metric.delta > 0 ? 'text-emerald-600' : metric.delta < 0 ? 'text-red-600' : 'text-text-muted'}`}>{metric.delta > 0 ? '↑' : metric.delta < 0 ? '↓' : '→'} {metric.delta >= 0 ? '+' : ''}{metric.delta.toFixed(4)}</span></Td>
                </tr>
              })}
            </tbody>
          </TableWrap>
        </div>)}
        {comparison.pass_rate_inference && <p className="mt-2 text-xs text-text-muted">配对推断（McNemar）：不一致样本 {comparison.pass_rate_inference.discordant_count}/{comparison.pass_rate_inference.sample_count}，通过率变化 {comparison.pass_rate_inference.pass_rate_delta >= 0 ? '+' : ''}{(comparison.pass_rate_inference.pass_rate_delta * 100).toFixed(1)}pp，p={comparison.pass_rate_inference.p_value.toFixed(4)}。</p>}
      </Card>

      <Card title="逐用例结果" subtitle="点击行查看对比侧详情；分数为该题主评分（无主分的 Runner 显示 —）。">
        {comparison.cases.length === 0 ? <Empty text="没有逐用例记录。" /> : <TableWrap minWidth={960}>
          <thead className="bg-bg-elevated"><tr><Th>用例</Th><Th>问题</Th><Th>基准状态</Th><Th>对比状态</Th><Th end>基准分</Th><Th end>对比分</Th><Th end>变化</Th><Th end>操作</Th></tr></thead>
          <tbody className="divide-y divide-border-subtle">
            {comparison.cases.map(item => {
              const improved = item.baseline !== 'pass' && item.candidate === 'pass'
              const regressed = item.baseline === 'pass' && item.candidate !== 'pass'
              const baselineScore = primaryScore(item.scores?.baseline)
              const candidateScore = primaryScore(item.scores?.candidate)
              return <tr key={item.case_id}>
                <Td mono>{item.case_id.slice(0, 8)}</Td>
                <Td className="max-w-[280px] truncate" >{item.question || '—'}</Td>
                <Td><Badge text={item.baseline === 'pass' ? '通过' : item.baseline === 'fail' ? '未通过' : item.baseline} tone={item.baseline === 'pass' ? 'good' : item.baseline === 'fail' ? 'bad' : 'warn'} /></Td>
                <Td><Badge text={item.candidate === 'pass' ? '通过' : item.candidate === 'fail' ? '未通过' : item.candidate} tone={item.candidate === 'pass' ? 'good' : item.candidate === 'fail' ? 'bad' : 'warn'} /></Td>
                <Td end className="tabular-nums">{formatScore(baselineScore)}</Td>
                <Td end className="tabular-nums">{formatScore(candidateScore)}</Td>
                <Td end><span className={improved ? 'font-medium text-emerald-600' : regressed ? 'font-medium text-red-600' : 'text-text-muted'}>{improved ? '↑ 改善' : regressed ? '↓ 退步' : '→ 持平'}</span></Td>
                <Td end><button type="button" className={ctlGhost} onClick={() => openCase(item.case_id)}>详情<ArrowRight size={11} className="ml-1 inline" aria-hidden="true" /></button></Td>
              </tr>
            })}
          </tbody>
        </TableWrap>}
      </Card>

      {caseDetail && <Card title="用例详情（对比侧）" subtitle={`修订 r${caseDetail.case_revision} · ${caseDetail.result.status === 'pass' ? '通过' : caseDetail.result.status === 'fail' ? '未通过' : caseDetail.result.status}`}>
        <div className="space-y-1.5 text-xs text-text-secondary">
          {caseDetail.trace_id && <p>Trace：<code className="rounded bg-bg-elevated px-1 py-0.5 font-mono text-[11px]">{caseDetail.trace_id}</code>
            <a className="ml-2 text-accent hover:underline" href={`/observability/monitoring?tab=traces&q=${encodeURIComponent(caseDetail.trace_id)}`}>查看完整 Trace</a>
            <span className="ml-2">时延 {caseDetail.latency_ms ?? '—'} ms · 费用 {caseDetail.cost_status === 'available' ? `${caseDetail.cost_amount} ${caseDetail.cost_currency || ''}` : '未归因'}</span></p>}
          <p><b className="text-text-primary">问题：</b>{String((caseDetail.result.expected as Record<string, unknown>).query ?? (caseDetail.result.actual as Record<string, unknown>).query ?? '—')}</p>
          <p><b className="text-text-primary">期望答案：</b>{(() => {
            const expected = caseDetail.result.expected as Record<string, unknown> | null
            const reference = String(expected?.reference_answer ?? expected?.answer ?? '').trim()
            const facts = (expected?.required_facts as string[] | undefined) || []
            if (!reference && !facts.length) return '—'
            return `${reference || '（无参考答案文本）'}${facts.length ? `（必需事实：${facts.join('、')}）` : ''}`
          })()}</p>
          <p><b className="text-text-primary">实际回答：</b>{String((caseDetail.result.actual as Record<string, unknown>).answer ?? '—')}</p>
          <p><b className="text-text-primary">评分理由：</b>{(() => {
            const actual = caseDetail.result.actual as Record<string, unknown> | null
            const reason = String(actual?.reason ?? actual?.rationale ?? actual?.judge_reason ?? '').trim()
            if (reason) return reason
            const metrics = caseDetail.result.metrics || {}
            const entries = Object.entries(metrics).map(([key, value]) => `${key}=${typeof value === 'number' ? value.toFixed(3) : '—'}`)
            return entries.length ? `（无评语，按指标判分：${entries.join(' · ')}）` : '无数值'
          })()}</p>
          {caseDetail.result.error_stage && <p><b className="text-text-primary">失败阶段：</b>{caseDetail.result.error_stage}</p>}
        </div>
        {(() => {
          const expectedObj = caseDetail.result.expected as Record<string, unknown> | null
          const anchors = (expectedObj?.evidence as Array<{ doc_id?: string }> | undefined) || []
          const truthDocs = new Set(anchors.map(a => String(a.doc_id || '')))
          const details = ((caseDetail.result.actual as Record<string, unknown> | null)?.details as Array<Record<string, unknown>> | undefined) || []
          if (!details.length) return null
          return <div className="mt-3">
            <p className="mb-1.5 text-xs font-medium text-text-muted">检索命中与判分裁决</p>
            <TableWrap minWidth={640}>
              <thead className="bg-bg-elevated"><tr><Th>#</Th><Th>文档 / 切片</Th><Th end>Rerank 分</Th><Th>判分裁决</Th><Th>摘要</Th></tr></thead>
              <tbody className="divide-y divide-border-subtle">
                {details.map((hit, i) => {
                  const denied = Boolean(hit.permission_denied)
                  const versionHidden = hit.version_visible === false
                  const verdict = denied
                    ? { text: '✕ 越权剔除 · 仅留痕', tone: 'bad' as const }
                    : versionHidden
                      ? { text: '⚠ 版本不可见 · 剔除判分', tone: 'warn' as const }
                      : truthDocs.has(String(hit.doc_id || ''))
                        ? { text: '✓ 计入召回', tone: 'good' as const }
                        : { text: '– 未命中真值', tone: 'gray' as const }
                  return <tr key={String(hit.chunk_id || i)}>
                    <Td end>{i + 1}</Td>
                    <Td><span className="block font-mono text-[11px] text-text-muted">{String(hit.doc_id || '')}</span>{String(hit.title || '')}</Td>
                    <Td end>{typeof hit.rerank_score === 'number' ? hit.rerank_score.toFixed(2) : '—'}</Td>
                    <Td><Badge text={verdict.text} tone={verdict.tone} /></Td>
                    <Td className="max-w-[260px]">{String(hit.snippet || String(hit.page_content || '')).slice(0, 120)}{String(hit.snippet || String(hit.page_content || '')).length > 120 ? '…' : ''}</Td>
                  </tr>
                })}
              </tbody>
            </TableWrap>
            <p className="mt-1.5 text-xs text-text-muted">越权与版本不可见的切片仍保留在命中列表供审计，但不参与召回判分。</p>
          </div>
        })()}
      </Card>}

      <p className="text-xs text-text-muted">对比仅在相同数据集版本与相同评测方式的运行间有效；RAGAS 指标仅在该运行的评测方式包含 RAGAS 时产出。</p>
    </>}
  </div>
}
