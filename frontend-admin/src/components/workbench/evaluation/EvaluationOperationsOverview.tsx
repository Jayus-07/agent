'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, RefreshCw } from 'lucide-react'
import { evaluationService, type EvaluationV2Candidate, type EvaluationV2Dataset, type EvaluationV2Run } from '@/api/evaluation'

interface OverviewData {
  datasets: EvaluationV2Dataset[]
  candidates: EvaluationV2Candidate[]
  runs: EvaluationV2Run[]
}

interface Props {
  onNavigate: (section: 'datasets' | 'review' | 'runs' | 'compare') => void
}

function formatPercent(value: number | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 }).format(value)
    : '—'
}

function runStatusLabel(run: EvaluationV2Run): string {
  if (run.job_status === 'completed' && run.gate_verdict === 'PASS') return '有效 · Gate 通过'
  if (run.job_status === 'completed' && run.gate_verdict === 'FAIL') return '有效 · Gate 未通过'
  if (run.gate_verdict === 'INVALID') return '运行无效'
  return run.job_status || '状态未知'
}

function MetricCard({ title, value, detail, tone }: { title: string; value: string; detail: string; tone?: 'warn' }) {
  return (
    <article className="min-w-0 rounded-lg border border-border-subtle bg-surface-base p-4">
      <p className="text-sm text-text-secondary">{title}</p>
      <p className={'mt-2 text-2xl font-semibold tabular-nums ' + (tone === 'warn' ? 'text-amber-700' : 'text-text-primary')}>{value}</p>
      <p className="mt-1 text-xs leading-5 text-text-muted">{detail}</p>
    </article>
  )
}

export default function EvaluationOperationsOverview({ onNavigate }: Props) {
  const [data, setData] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [datasets, candidates, runs] = await Promise.all([
        evaluationService.listV2Datasets(100),
        evaluationService.listV2Candidates(undefined, 200),
        evaluationService.listV2Runs(50),
      ])
      setData({ datasets, candidates, runs })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取评测概览失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  if (loading && !data) {
    return <p className="rounded-lg border border-border-subtle bg-surface-base p-6 text-sm text-text-secondary" role="status">正在读取真实评测资产与运行记录…</p>
  }
  if (error && !data) {
    return (
      <div className="rounded-lg border border-red-200 bg-surface-base p-5" role="alert">
        <p className="text-sm font-medium text-red-800">评测概览读取失败</p>
        <p className="mt-1 break-words text-sm text-text-secondary">{error}</p>
        <button type="button" onClick={() => void load()} className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-md border border-border-subtle px-3 text-sm text-text-primary hover:bg-bg-hover">
          <RefreshCw size={15} /> 重试
        </button>
      </div>
    )
  }

  const snapshot = data || { datasets: [], candidates: [], runs: [] }
  const pendingCandidates = snapshot.candidates.filter(item => !['approved', 'rejected'].includes(item.review_status))
  const validRuns = snapshot.runs.filter(run => run.job_status === 'completed' && ['PASS', 'FAIL'].includes(run.gate_verdict || '') && run.total_cases > 0)
  const latestValid = validRuns[0]
  const invalidRuns = snapshot.runs.filter(run => run.gate_verdict === 'INVALID' || run.job_status === 'blocked')
  const totalPublished = snapshot.datasets.filter(dataset => Number(dataset.version_count || 0) > 0).length
  const pendingCount = pendingCandidates.length
  const trend = validRuns.slice(0, 7).reverse()

  return (
    <div className="space-y-5">
      {error && <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">{error} · 当前显示上次成功读取的数据。</p>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="已发布数据集" value={String(totalPublished)} detail="包含内置基准与已审核业务版本" />
        <MetricCard title="等待人工审核" value={String(pendingCount)} detail="候选与待审核导入版本" tone={pendingCount > 0 ? 'warn' : undefined} />
        <MetricCard title="最近有效通过率" value={latestValid ? formatPercent(latestValid.passed_cases / Math.max(1, latestValid.total_cases)) : '暂无'} detail={latestValid ? latestValid.run_id + ' · ' + latestValid.total_cases + ' 条有效样本' : '尚无可用于质量判断的有效运行'} />
        <MetricCard title="近期无效运行" value={String(invalidRuns.length)} detail="环境或输入无效不会作为质量失败" tone={invalidRuns.length > 0 ? 'warn' : undefined} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.85fr)]">
        <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-trend-heading">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="evaluation-trend-heading" className="text-base font-semibold text-text-primary">质量趋势</h2>
              <p className="mt-1 text-sm text-text-secondary">只统计有效且已完成的运行；无效运行不进入分数趋势。</p>
            </div>
            <button type="button" onClick={() => onNavigate('runs')} className="inline-flex min-h-10 items-center gap-1 rounded-md px-2 text-sm font-medium text-accent hover:bg-accent-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              查看运行 <ArrowRight size={15} />
            </button>
          </div>
          {trend.length === 0 ? (
            <div className="mt-4 rounded-md bg-bg-elevated px-4 py-8 text-center text-sm text-text-secondary">最近没有可展示的有效运行。先完成数据集审核并运行已固定版本的评测。</div>
          ) : (
            <div className="mt-5">
              <p className="sr-only">最近有效运行通过率：{trend.map(run => run.run_id + ' ' + formatPercent(run.passed_cases / Math.max(1, run.total_cases))).join('；')}</p>
              <div className="flex h-44 items-end gap-3 border-b border-l border-border-subtle px-3 pb-2" role="img" aria-label="最近有效运行通过率柱状图">
                {trend.map(run => {
                  const passRate = run.passed_cases / Math.max(1, run.total_cases)
                  const rate = Math.max(0, Math.min(1, Number(passRate) || 0))
                  return (
                    <div key={run.run_id} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1">
                      <span className="text-xs tabular-nums text-text-secondary">{formatPercent(passRate)}</span>
                      <div className="w-full max-w-12 rounded-t bg-accent" style={{ height: Math.max(4, Math.round(rate * 112)) + 'px' }} />
                      <span className="max-w-full truncate text-[11px] text-text-muted" title={run.run_id}>{run.run_id.slice(0, 8)}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </section>

        <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-next-heading">
          <h2 id="evaluation-next-heading" className="text-base font-semibold text-text-primary">待办与运行记录</h2>
          <p className="mt-1 text-sm text-text-secondary">从资产核验走到可复现的质量判断。</p>
          {pendingCount > 0 ? (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3">
              <p className="text-sm font-medium text-amber-900">有 {pendingCount} 项等待审核</p>
              <button type="button" onClick={() => onNavigate('review')} className="mt-2 inline-flex min-h-10 items-center gap-1 text-sm font-medium text-accent">
                打开标注审核 <ArrowRight size={15} />
              </button>
            </div>
          ) : (
            <p className="mt-4 rounded-md bg-bg-elevated p-3 text-sm text-text-secondary">当前没有待审核候选。</p>
          )}
          <div className="mt-4 divide-y divide-border-subtle">
            {snapshot.runs.slice(0, 4).map(run => (
              <button key={run.run_id} type="button" onClick={() => onNavigate('runs')} className="flex min-h-14 w-full items-center justify-between gap-3 py-2 text-left hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-text-primary">{run.suite_name || run.suite_key || '评测运行'}</span>
                  <span className="block truncate text-xs text-text-muted">{run.run_id} · {run.suite_version || '方案版本未记录'}</span>
                </span>
                <span className="shrink-0 text-xs text-text-secondary">{runStatusLabel(run)}</span>
              </button>
            ))}
            {snapshot.runs.length === 0 && <p className="py-5 text-sm text-text-secondary">暂无运行记录。</p>}
          </div>
        </section>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-lg border border-border-subtle bg-surface-base p-4">
          <h2 className="text-base font-semibold text-text-primary">数据资产</h2>
          <p className="mt-1 text-sm text-text-secondary">当前读取 {snapshot.datasets.length} 个 V2 逻辑数据集，其中 {totalPublished} 个至少有一个已发布 Golden 版本。</p>
          <button type="button" onClick={() => onNavigate('datasets')} className="mt-3 inline-flex min-h-10 items-center gap-1 text-sm font-medium text-accent">
            打开数据集中心 <ArrowRight size={15} />
          </button>
        </section>
        <section className="rounded-lg border border-border-subtle bg-surface-base p-4">
          <h2 className="text-base font-semibold text-text-primary">实验比较</h2>
          <p className="mt-1 text-sm leading-6 text-text-secondary">只有运行版本、Suite、证据集和评判规则一致时才展示变化结论。缺少固定快照时会说明不可比较。</p>
          <button type="button" onClick={() => onNavigate('compare')} className="mt-3 inline-flex min-h-10 items-center gap-1 text-sm font-medium text-accent">
            比较实验 <ArrowRight size={15} />
          </button>
        </section>
      </div>
    </div>
  )
}
