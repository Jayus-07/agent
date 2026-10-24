'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, RefreshCw } from 'lucide-react'
import { evaluationService, type EvaluationV2Candidate, type EvaluationV2Dataset, type EvaluationV2Run } from '@/api/evaluation'
import { EvalButton, EvalCard, EvalEmpty, EvalInfoBox, EvalKpiCard, EvalMetricRow, EvalPageHeader, EvalTag } from './evalUi'

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

function runStatusTone(run: EvaluationV2Run): 'good' | 'bad' | 'warn' | 'dim' {
  if (run.job_status === 'completed' && run.gate_verdict === 'PASS') return 'good'
  if (run.job_status === 'completed' && run.gate_verdict === 'FAIL') return 'bad'
  if (run.gate_verdict === 'INVALID' || run.job_status === 'blocked') return 'warn'
  return 'dim'
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
        <EvalButton className="mt-3" onClick={() => void load()}>
          <RefreshCw size={15} /> 重试
        </EvalButton>
      </div>
    )
  }

  const snapshot = data || { datasets: [], candidates: [], runs: [] }
  const pendingCandidates = snapshot.candidates.filter(item => !['approved', 'rejected'].includes(item.review_status))
  const validRuns = snapshot.runs.filter(run => run.job_status === 'completed' && ['PASS', 'FAIL'].includes(run.gate_verdict || '') && run.total_cases > 0)
  const latestValid = validRuns[0]
  const invalidRuns = snapshot.runs.filter(run => run.gate_verdict === 'INVALID' || run.job_status === 'blocked')
  const failedRuns = snapshot.runs.filter(run => run.job_status === 'completed' && run.gate_verdict === 'FAIL')
  const totalPublished = snapshot.datasets.filter(dataset => Number(dataset.version_count || 0) > 0).length
  const pendingCount = pendingCandidates.length
  const trend = validRuns.slice(0, 7).reverse()

  return (
    <div>
      <EvalPageHeader
        title="质量运营总览"
        subtitle="从评测资产、运行有效性到发布裁决，一处了解企业 AI 的真实表现；无效运行不会伪装成质量结论。"
        actions={<EvalButton variant="primary" onClick={() => onNavigate('runs')}>＋ 创建实验</EvalButton>}
      />
      {error && <p className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">{error} · 当前显示上次成功读取的数据。</p>}

      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <EvalKpiCard label="正式数据集" value={String(totalPublished)} detail="Golden 版本冻结 · 全程可追溯" />
        <EvalKpiCard label="等待人工审核" value={String(pendingCount)} detail="候选与待审核导入版本 · 需要事实与证据核对" tone={pendingCount > 0 ? 'warn' : undefined} />
        <EvalKpiCard
          label="最近有效通过率"
          value={latestValid ? formatPercent(latestValid.passed_cases / Math.max(1, latestValid.total_cases)) : '暂无'}
          detail={latestValid ? latestValid.run_id.slice(0, 8) + ' · ' + latestValid.total_cases + ' 条有效样本' : '尚无可用于质量判断的有效运行'}
          tone={latestValid ? 'good' : undefined}
        />
        <EvalKpiCard label="发布阻断" value={String(failedRuns.length)} detail="安全否决不能被平均分抵消" tone={failedRuns.length > 0 ? 'bad' : undefined} />
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(280px,1fr)]">
        <EvalCard
          title="质量趋势 · 最近有效运行"
          subtitle="只统计有效且已完成的运行；无效运行不进入分数趋势。"
          actions={<EvalTag>最近 {trend.length} 次</EvalTag>}
        >
          {trend.length === 0 ? (
            <EvalEmpty>最近没有可展示的有效运行。先完成数据集审核并运行已固定版本的评测。</EvalEmpty>
          ) : (
            <div className="mt-2">
              <p className="sr-only">最近有效运行通过率：{trend.map(run => run.run_id + ' ' + formatPercent(run.passed_cases / Math.max(1, run.total_cases))).join('；')}</p>
              <div className="flex h-52 items-end gap-4 border-b border-border-subtle px-4 pb-2" role="img" aria-label="最近有效运行通过率柱状图">
                {trend.map(run => {
                  const passRate = run.passed_cases / Math.max(1, run.total_cases)
                  const rate = Math.max(0, Math.min(1, Number(passRate) || 0))
                  return (
                    <div key={run.run_id} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1.5">
                      <span className="text-xs tabular-nums text-text-secondary">{formatPercent(passRate)}</span>
                      <div className="w-full max-w-12 rounded-t-md bg-accent" style={{ height: Math.max(6, Math.round(rate * 130)) + 'px' }} />
                      <span className="max-w-full truncate text-[11px] text-text-muted" title={run.run_id}>{run.run_id.slice(0, 8)}</span>
                    </div>
                  )
                })}
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <EvalTag>检索召回</EvalTag>
                <EvalTag>证据引用</EvalTag>
                <EvalTag>安全拒答</EvalTag>
              </div>
            </div>
          )}
        </EvalCard>

        <EvalCard
          title="待办与运行记录"
          subtitle="从资产核验走到可复现的质量判断。"
          actions={<button type="button" onClick={() => onNavigate('review')} className="text-xs font-semibold text-accent hover:underline">打开审核台 →</button>}
        >
          {pendingCount > 0 ? (
            <button type="button" onClick={() => onNavigate('review')} className="mb-2 flex w-full items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-left hover:border-amber-300">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-amber-100 font-bold text-amber-800">!</span>
              <span className="min-w-0">
                <strong className="block text-sm text-amber-950">有 {pendingCount} 项等待审核</strong>
                <span className="block truncate text-xs text-amber-800">候选事实与证据需要人工核对后才能发布 Golden</span>
              </span>
            </button>
          ) : (
            <p className="mb-2 rounded-lg bg-bg-elevated p-3 text-xs text-text-secondary">当前没有待审核候选。</p>
          )}
          <div className="divide-y divide-border-subtle">
            {snapshot.runs.slice(0, 4).map(run => (
              <button key={run.run_id} type="button" onClick={() => onNavigate('runs')} className="flex w-full items-center gap-3 py-3 text-left hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                <span className={'grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold ' + (runStatusTone(run) === 'good' ? 'bg-emerald-50 text-emerald-700' : runStatusTone(run) === 'bad' ? 'bg-red-50 text-red-700' : 'bg-bg-elevated text-text-secondary')}>
                  {runStatusTone(run) === 'bad' ? '!' : '✓'}
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm text-text-primary">{run.suite_name || run.suite_key || '评测运行'}</strong>
                  <span className="block truncate text-xs text-text-muted">{run.run_id.slice(0, 8)} · {run.suite_version || '方案版本未记录'}</span>
                </span>
                <span className="shrink-0 text-xs text-text-secondary">{runStatusLabel(run)}</span>
              </button>
            ))}
            {snapshot.runs.length === 0 && <p className="py-5 text-sm text-text-secondary">暂无运行记录。</p>}
          </div>
          <div className="mt-3">
            <EvalInfoBox>
              质量分数不是独立的发布许可证。只有固定数据集、运行环境有效、强制指标达标且安全检查通过，才能形成有效的 Gate 决策。
            </EvalInfoBox>
          </div>
        </EvalCard>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <EvalCard title="数据资产分布" subtitle="当前读取真实 V2 逻辑数据集，不使用演示数据。">
          {snapshot.datasets.length === 0 ? (
            <EvalEmpty>尚无 V2 数据集，先到数据集中心创建。</EvalEmpty>
          ) : (
            snapshot.datasets.slice(0, 5).map(dataset => (
              <EvalMetricRow key={dataset.dataset_id} label={dataset.name || dataset.dataset_key}>
                {Number(dataset.version_count || 0) > 0 ? dataset.version_count + ' 个已发布版本' : '未发布'}
              </EvalMetricRow>
            ))
          )}
          <EvalButton size="sm" className="mt-3" onClick={() => onNavigate('datasets')}>打开数据集中心</EvalButton>
        </EvalCard>
        <EvalCard title="下一步建议" subtitle="建议先走通「导入候选 → 审核 → 发布 Golden → 运行实验 → 比较基线」的完整链路。">
          <p className="text-sm leading-6 text-text-secondary">
            运行必须绑定已发布方案与不可变 Golden；对比只在 Suite、数据集与语料快照一致时给出结论。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <EvalButton variant="soft" onClick={() => onNavigate('datasets')}>导入评测候选</EvalButton>
            <EvalButton onClick={() => onNavigate('compare')}>查看 A/B 对比</EvalButton>
          </div>
        </EvalCard>
      </div>

      <p className="mt-5 text-xs leading-6 text-text-muted">
        本页全部指标来自服务端持久化数据；运行有效性（INVALID/BLOCKED）与质量裁决（PASS/FAIL）分别展示，页面不代算分数。
      </p>
    </div>
  )
}
