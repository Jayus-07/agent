'use client'

/**
 * 评测运行详情面板：发布评测（release.eval_run_id）与 CLI 台账 run 共用。
 *
 * 数据源 GET /api/evaluation/runs/{run_id}（脱敏报告）——与 V2 运行的
 * RunHistoryDetail 不同，该视图读报告文件口径（M7：明细权威在文件），
 * 覆盖不经 V2 表的两类评测记录。2026-11-22 评测中心链路补齐。
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RotateCw } from 'lucide-react'
import { evaluationService } from '@/api/evaluation'
import { errMessage } from '@/api/client'
import { Badge, Card, ctl, ctlGhost, Empty, StatusBadge, TableWrap, Td, Th, timeShort } from './evalUi'

const RESULT_LIMIT = 100

function statusTone(status: string): 'good' | 'bad' | 'warn' | 'accent' {
  if (status === 'pass') return 'good'
  if (status === 'fail') return 'bad'
  if (status === 'error') return 'warn'
  return 'accent'
}

export default function EvalRunDetailPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [expandedCase, setExpandedCase] = useState('')
  const detailQuery = useQuery({
    queryKey: ['admin', 'evaluation-run-detail', runId],
    queryFn: () => evaluationService.getRun(runId),
  })

  if (detailQuery.isPending) {
    return <Card title={`评测详情 · ${runId.slice(0, 16)}`} actions={<button type="button" className={ctlGhost} onClick={onClose}>返回列表</button>}>
      <Empty text="正在读取评测报告…" />
    </Card>
  }
  if (detailQuery.isError) {
    return <Card title={`评测详情 · ${runId.slice(0, 16)}`} actions={<button type="button" className={ctlGhost} onClick={onClose}>返回列表</button>}>
      <div className="space-y-2 py-5 text-center">
        <p role="alert" className="text-xs text-red-700">{errMessage(detailQuery.error)}</p>
        <button type="button" className={ctl} onClick={() => void detailQuery.refetch()}>重新读取</button>
      </div>
    </Card>
  }

  const detail = detailQuery.data
  const report = detail?.report
  const summary = report?.summaries?.find(item => item.module === report.module) ?? report?.summaries?.[0]
  const validity = (report?.metadata as { run_validity?: { validity?: string; invalid_reason?: string } } | undefined)?.run_validity
  const results = report?.results ?? []
  const visibleResults = results.slice(0, RESULT_LIMIT)

  return <Card
    title={`评测详情 · ${detail?.run_id ?? runId}`}
    subtitle={`${report?.module ?? '—'} · ${report?.mode ?? '—'}${validity?.validity ? ` · 有效性 ${validity.validity}` : ''}${validity?.invalid_reason ? `（${validity.invalid_reason}）` : ''}`}
    actions={<button type="button" className={ctlGhost} disabled={detailQuery.isFetching} onClick={() => void detailQuery.refetch()}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />刷新</button>}
  >
    {summary ? <div className="mb-4 grid gap-3 text-xs sm:grid-cols-4">
      <div className="rounded-lg border border-border-subtle p-3">
        <p className="text-text-muted">样本</p>
        <p className="text-sm text-text-primary">{summary.passed}/{summary.total} 通过</p>
        <p className="text-[11px] text-text-muted">失败 {summary.failed} · 错误 {summary.errors} · 跳过 {summary.skipped}</p>
      </div>
      <div className="rounded-lg border border-border-subtle p-3">
        <p className="text-text-muted">通过率</p>
        <p className="text-sm text-text-primary">{(summary.pass_rate * 100).toFixed(1)}%</p>
      </div>
      {!!report?.tier_summaries?.length && <div className="rounded-lg border border-border-subtle p-3 sm:col-span-2">
        <p className="text-text-muted">分层门禁</p>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {report.tier_summaries.map(tier => <span key={tier.tier} className="inline-flex items-center gap-1 text-[11px]">
            <Badge text={`${tier.tier} ${(tier.pass_rate * 100).toFixed(0)}%`} tone={tier.passed_threshold ? 'good' : 'bad'} />
            <span className="text-text-muted">{tier.passed}/{tier.total}</span>
          </span>)}
        </div>
      </div>}
    </div> : null}

    {results.length === 0 ? <Empty text="报告无逐题结果。" /> : <>
      <TableWrap minWidth={860}>
        <thead className="bg-bg-elevated"><tr><Th>用例</Th><Th>状态</Th><Th>失败阶段</Th><Th>说明</Th><Th end>耗时</Th><Th>时间戳</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {visibleResults.map(result => <tr
            key={result.case_id}
            className={expandedCase === result.case_id ? 'bg-bg-hover' : 'cursor-pointer'}
            onClick={() => setExpandedCase(current => current === result.case_id ? '' : result.case_id)}
          >
            <Td mono>{result.case_id}</Td>
            <Td><Badge text={result.status} tone={statusTone(result.status)} /></Td>
            <Td>{result.error_stage ? <Badge text={result.error_stage} tone="warn" /> : <span className="text-text-muted">—</span>}</Td>
            <Td className="max-w-72 break-words">{result.error_msg || <span className="text-text-muted">—</span>}</Td>
            <Td end>{result.duration_ms ? `${result.duration_ms}ms` : '—'}</Td>
            <Td>{timeShort(detail?.report.timestamp ?? null)}</Td>
          </tr>)}
        </tbody>
      </TableWrap>
      {results.length > RESULT_LIMIT && <p className="mt-2 text-[11px] text-text-muted">仅显示前 {RESULT_LIMIT} 条（共 {results.length} 条）。</p>}
      {expandedCase && (() => {
        const result = results.find(item => item.case_id === expandedCase)
        if (!result) return null
        return <div className="mt-3 rounded-lg border border-border-subtle p-3">
          <p className="mb-2 text-xs font-medium text-text-secondary">用例 {result.case_id} 明细（展示层已脱敏）</p>
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <p className="text-[11px] text-text-muted mb-1">期望（expected）</p>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-black/[0.03] p-2 text-[10px] font-mono text-text-primary">{JSON.stringify(result.expected, null, 2)}</pre>
            </div>
            <div>
              <p className="text-[11px] text-text-muted mb-1">实际（actual）</p>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-black/[0.03] p-2 text-[10px] font-mono text-text-primary">{JSON.stringify(result.actual, null, 2)}</pre>
            </div>
          </div>
          {Object.keys(result.metrics ?? {}).length > 0 && <p className="mt-2 text-[11px] text-text-muted">指标：{JSON.stringify(result.metrics)}</p>}
        </div>
      })()}
    </>}
  </Card>
}
