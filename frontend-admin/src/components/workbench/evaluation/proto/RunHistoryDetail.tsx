'use client'

import { useState, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, RotateCw } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { evaluationService, type EvaluationV2RunResult } from '@/api/evaluation'
import { errMessage } from '@/api/client'
import EvaluationV2RunCaseDetailButton from '@/components/workbench/evaluation/EvaluationV2RunCaseDetailButton'
import { Card, ctl, ctlGhost, Empty, fmtCost, modeShort, StatusBadge, TableWrap, Td, Th, timeShort } from './evalUi'

const RESULT_PAGE_SIZE = 10
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked'])

function primaryScore(scores: EvaluationV2RunResult['scores']): number | null {
  for (const key of ['overall', 'score', 'pass_score', 'judge_score', 'total_score']) {
    const value = scores[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

function elapsedText(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const start = new Date(startedAt).getTime()
  const finish = new Date(finishedAt).getTime()
  return Number.isFinite(start) && Number.isFinite(finish) && finish >= start
    ? `${Math.max(1, Math.round((finish - start) / 1000))} 秒`
    : '—'
}

function SummaryValue({ label, children }: { label: string; children: ReactNode }) {
  return <div className="min-w-0 rounded-md bg-bg-elevated px-3 py-2.5">
    <p className="text-[11px] text-text-muted">{label}</p>
    <div className="mt-1 break-words text-xs font-medium text-text-primary">{children}</div>
  </div>
}

export default function RunHistoryDetail({ runId, onBack, onGoCompare }: {
  runId: string
  onBack: () => void
  onGoCompare?: (runId: string) => void
}) {
  const [page, setPage] = useState(0)
  const runQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-run', runId],
    queryFn: () => evaluationService.getV2Run(runId),
    refetchInterval: query => {
      const status = query.state.data?.run.job_status
      return status && !TERMINAL.has(status) ? 3000 : false
    },
  })
  const run = runQuery.data?.run
  const resultsQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-run-results', runId, page],
    queryFn: () => evaluationService.listV2RunResults(runId, RESULT_PAGE_SIZE, page * RESULT_PAGE_SIZE),
    enabled: Boolean(run),
    refetchInterval: run && !TERMINAL.has(run.job_status) ? 3000 : false,
  })
  const results = resultsQuery.data ?? []
  const completedCases = run?.completed_cases ?? 0
  const pageCount = Math.max(1, Math.ceil(completedCases / RESULT_PAGE_SIZE))

  if (runQuery.isPending) return <Card title="运行详情"><Empty text="正在读取运行信息…" /></Card>
  if (runQuery.isError || !run) return <Card title="运行详情">
    <div className="space-y-3">
      <p role="alert" className="text-xs text-red-700">{runQuery.error ? errMessage(runQuery.error) : '运行记录不存在或当前账号无权查看。'}</p>
      <div className="flex gap-2">
        <button type="button" className={ctl} onClick={() => void runQuery.refetch()}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />重试</button>
        <button type="button" className={ctlGhost} onClick={onBack}>返回运行记录</button>
      </div>
    </div>
  </Card>

  const fingerprint = runQuery.data.fingerprint
  const snapshotEvaluationMode = typeof run.snapshot_json?.evaluation_mode === 'string'
    ? run.snapshot_json.evaluation_mode
    : null
  const evaluationMode = run.evaluation_mode || snapshotEvaluationMode || fingerprint.evaluation_mode
  const passRate = completedCases > 0 ? `${Math.round((run.passed_cases / completedCases) * 100)}%` : '—'

  return <div className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <button type="button" className={`${ctlGhost} -ml-2 mb-1`} onClick={onBack}>
          <ArrowLeft size={13} className="mr-1 inline" aria-hidden="true" />返回运行记录
        </button>
        <h2 className="text-base font-semibold text-text-primary">运行详情</h2>
        <p className="mt-1 break-all font-mono text-[11px] text-text-muted">{run.run_id}</p>
      </div>
      {run.job_status === 'completed' && onGoCompare && <button type="button" className={ctl} onClick={() => onGoCompare(run.run_id)}>
        对比运行<ArrowRight size={12} className="ml-1 inline" aria-hidden="true" />
      </button>}
    </header>

    <Card title="运行摘要" subtitle="状态、样本进度和费用来自服务端运行记录。">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryValue label="状态"><StatusBadge status={run.job_status} /></SummaryValue>
        <SummaryValue label="数据集">{run.suite_name || run.suite_key || '—'} · v{run.suite_version || '—'}</SummaryValue>
        <SummaryValue label="运行方式">{modeShort(evaluationMode)}</SummaryValue>
        <SummaryValue label="通过率">{passRate}</SummaryValue>
        <SummaryValue label="样本进度">{run.completed_cases} / {run.total_cases} · 通过 {run.passed_cases}</SummaryValue>
        <SummaryValue label="总费用">{fmtCost(run.cost_amount, run.cost_currency)}</SummaryValue>
        <SummaryValue label="开始时间">{timeShort(run.started_at || run.created_at)}</SummaryValue>
        <SummaryValue label="耗时">{elapsedText(run.started_at, run.finished_at)}</SummaryValue>
      </div>
      {run.invalid_reason && <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900">运行无效：{run.invalid_reason}</p>}
    </Card>

    <Card title="运行环境指纹" subtitle="本次运行冻结的模型、Prompt、数据集和语料快照。">
      <dl className="grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
        <div><dt className="text-text-muted">Runner</dt><dd className="mt-0.5 break-words text-text-primary">{fingerprint.runner || '—'}</dd></div>
        <div><dt className="text-text-muted">生成模型</dt><dd className="mt-0.5 break-words text-text-primary">{fingerprint.generation_model || '未记录'}</dd></div>
        <div><dt className="text-text-muted">Embedding</dt><dd className="mt-0.5 break-words text-text-primary">{fingerprint.embedding_model || '—'}</dd></div>
        <div><dt className="text-text-muted">Rerank</dt><dd className="mt-0.5 break-words text-text-primary">{fingerprint.rerank_model || '—'}</dd></div>
        <div><dt className="text-text-muted">数据集版本</dt><dd className="mt-0.5 break-all font-mono text-[11px] text-text-primary">{fingerprint.dataset_version_id || '—'}</dd></div>
        <div><dt className="text-text-muted">语料快照</dt><dd className="mt-0.5 break-all font-mono text-[11px] text-text-primary">{fingerprint.corpus_snapshot_id || '—'}</dd></div>
        <div className="sm:col-span-2"><dt className="text-text-muted">Prompt 版本</dt><dd className="mt-0.5 break-words text-text-primary">{Object.keys(fingerprint.prompt_versions || {}).length
          ? Object.entries(fingerprint.prompt_versions || {}).map(([key, value]) => `${key}@${String(value)}`).join(' · ')
          : '未指定'}</dd></div>
      </dl>
    </Card>

    <Card title="逐用例结果" subtitle="执行错误和跳过样本会单独标记；查看单条记录可展开答案、评分、证据和 Trace。">
      {resultsQuery.isPending ? <Empty text="正在读取逐题结果…" />
        : resultsQuery.isError ? <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-200 p-3 text-xs text-red-700">
          <span role="alert">{errMessage(resultsQuery.error)}</span>
          <button type="button" className={ctlGhost} onClick={() => void resultsQuery.refetch()}>重新读取</button>
        </div>
          : results.length === 0 ? <Empty text={run.job_status === 'completed' ? '本次运行没有逐题结果。' : '运行尚未产出逐题结果，页面会自动更新。'} />
            : <TableWrap minWidth={760}>
              <thead className="bg-bg-elevated"><tr><Th>用例</Th><Th>修订</Th><Th>结果</Th><Th end>主评分</Th><Th>失败阶段</Th><Th end>时延</Th><Th>操作</Th></tr></thead>
              <tbody className="divide-y divide-border-subtle">
                {results.map(result => {
                  const score = primaryScore(result.scores)
                  return <tr key={result.result_id}>
                    <Td mono>{result.case_id.slice(0, 8)}</Td>
                    <Td>r{result.case_revision}</Td>
                    <Td><StatusBadge status={result.status} /></Td>
                    <Td end>{score === null ? '—' : score.toFixed(2)}</Td>
                    <Td>{result.failure_stage || '—'}</Td>
                    <Td end>{typeof result.latency_ms === 'number' ? `${result.latency_ms} ms` : '—'}</Td>
                    <Td><EvaluationV2RunCaseDetailButton runId={run.run_id} caseId={result.case_id} /></Td>
                  </tr>
                })}
              </tbody>
            </TableWrap>}
      {completedCases > 0 && <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border-subtle pt-3">
        <p className="text-[11px] text-text-muted">已完成 {completedCases} / {run.total_cases} 条 · 每页 {RESULT_PAGE_SIZE} 条</p>
        <div className="flex items-center gap-2">
          <button type="button" className={ctl} disabled={page === 0 || resultsQuery.isFetching} onClick={() => setPage(current => Math.max(0, current - 1))}>上一页</button>
          <span className="min-w-20 text-center text-[11px] text-text-muted">第 {Math.min(page + 1, pageCount)} / {pageCount} 页</span>
          <button type="button" className={ctl} disabled={page >= pageCount - 1 || resultsQuery.isFetching} onClick={() => setPage(current => Math.min(pageCount - 1, current + 1))}>下一页</button>
        </div>
      </div>}
    </Card>
  </div>
}
