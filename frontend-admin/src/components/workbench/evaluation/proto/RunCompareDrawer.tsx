'use client'

/**
 * 发布评测运行对比抽屉（批次 6c）：同 prompt_key 历史对比（方案 §6，2026-11-23 并入 6c）。
 *
 * - 数据面纯前端：复用 GET /api/evaluation/runs/{run_id}（脱敏报告）× 2，
 *   不新增后端端点、不依赖 V2 compare 接口（生产 V2 运行恒空，故不复用旧 CompareTab）。
 * - 归类口径：基准 pass → 对比非 pass 记「退步」（红）；基准非 pass → 对比 pass 记「改善」（绿）；
 *   error/skip 与 fail 同按"未通过"处理；同态用例默认折叠。
 * - 两侧 eval_run_id 缺失（CLI 本地执行失败等）时该侧禁选，不伪造对比。
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { evaluationService } from '@/api/evaluation'
import type { EvalResultDetail, EvalRunDetail } from '@/api/evaluation'
import { errMessage } from '@/api/client'
import type { PromptReleaseRecord } from '@/api/prompts'
import { Badge, ctl, Drawer, Empty, TableWrap, Td, Th, timeShort } from './evalUi'

/** 对比行归类：异常优先展示（退步 > 改善 > 双挂 > 单侧 > 双过）。 */
export type CaseDiffKind = 'regressed' | 'improved' | 'both_fail' | 'baseline_only' | 'candidate_only' | 'both_pass'

export interface CaseDiffRow {
  caseId: string
  kind: CaseDiffKind
  baseline: EvalResultDetail | null
  candidate: EvalResultDetail | null
}

const KIND_ORDER: Record<CaseDiffKind, number> = {
  regressed: 0,
  improved: 1,
  both_fail: 2,
  baseline_only: 3,
  candidate_only: 4,
  both_pass: 5,
}

export const CASE_DIFF_KIND_LABELS: Record<CaseDiffKind, string> = {
  regressed: '退步',
  improved: '改善',
  both_fail: '两侧均未通过',
  baseline_only: '仅基准侧',
  candidate_only: '仅对比侧',
  both_pass: '两侧通过',
}

const isPass = (result: EvalResultDetail | null | undefined) => result?.status === 'pass'

/**
 * 逐用例 diff：两 run 的 results 按 case_id 对齐归类。
 * 排序按异常优先（regressed 最前），同组内按 caseId 稳定排序。
 */
export function buildCaseDiff(baseline: EvalResultDetail[], candidate: EvalResultDetail[]): CaseDiffRow[] {
  const baselineById = new Map(baseline.map(item => [item.case_id, item]))
  const candidateById = new Map(candidate.map(item => [item.case_id, item]))
  const rows: CaseDiffRow[] = []
  for (const caseId of new Set([...baselineById.keys(), ...candidateById.keys()])) {
    const baselineItem = baselineById.get(caseId) ?? null
    const candidateItem = candidateById.get(caseId) ?? null
    const kind: CaseDiffKind = !baselineItem
      ? 'candidate_only'
      : !candidateItem
        ? 'baseline_only'
        : isPass(baselineItem) && !isPass(candidateItem)
          ? 'regressed'
          : !isPass(baselineItem) && isPass(candidateItem)
            ? 'improved'
            : isPass(baselineItem) && isPass(candidateItem)
              ? 'both_pass'
              : 'both_fail'
    rows.push({ caseId, kind, baseline: baselineItem, candidate: candidateItem })
  }
  return rows.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.caseId.localeCompare(b.caseId))
}

/** 默认基准选择：除预选对比侧外，最新一条已发布/已批准的记录；否则取最新一条。 */
function pickDefaultBaseline(releases: PromptReleaseRecord[], focusReleaseId: string): string {
  const candidates = releases.filter(item => item.release_id !== focusReleaseId && item.eval_run_id)
  const published = candidates.filter(item => ['published', 'approved', 'passed'].includes(item.status))
  const pool = published.length > 0 ? published : candidates
  return pool[0]?.release_id ?? ''
}

function ReleaseRunSummary({ label, detail, loading }: { label: string; detail: EvalRunDetail | undefined; loading: boolean }) {
  const report = detail?.report
  const summary = report?.summaries?.find(item => item.module === report.module) ?? report?.summaries?.[0]
  const validity = (report?.metadata as { run_validity?: { validity?: string; invalid_reason?: string } } | undefined)?.run_validity
  return <div className="min-w-0 rounded-lg border border-border-subtle bg-surface-base p-3">
    <div className="flex items-center gap-2"><Badge text={label} tone={label === '基准' ? 'gray' : 'accent'} />{detail && <span className="truncate font-mono text-[11px] text-text-muted">{detail.run_id}</span>}</div>
    {loading && <p role="status" className="mt-2 text-xs text-text-secondary">正在读取评测报告…</p>}
    {!loading && !summary && <p className="mt-2 text-xs text-text-muted">无汇总数据（报告缺失或执行失败）。</p>}
    {summary && <div className="mt-2 space-y-1 text-xs">
      <p className="text-text-secondary">
        通过率 <b className="text-text-primary">{(summary.pass_rate * 100).toFixed(1)}%</b>
        <span className="text-text-muted"> · {summary.passed}/{summary.total} 题 · 失败 {summary.failed} · 错误 {summary.errors}</span>
      </p>
      <p className="text-[11px] text-text-muted">{report?.module ?? '—'} · {report?.mode ?? '—'} · {timeShort(report?.timestamp ?? null)}{validity?.validity ? ` · 有效性 ${validity.validity}` : ''}</p>
    </div>}
  </div>
}

export default function RunCompareDrawer({ releases, focusReleaseId, onClose }: {
  /** 同 prompt_key 的发布评测记录（调用方已按域/key 过滤） */
  releases: PromptReleaseRecord[]
  /** 用户点"对比"的记录——默认作对比侧（candidate） */
  focusReleaseId: string
  onClose: () => void
}) {
  const selectable = useMemo(() => releases.filter(item => item.eval_run_id), [releases])
  const [baselineId, setBaselineId] = useState(() => pickDefaultBaseline(releases, focusReleaseId))
  const [candidateId, setCandidateId] = useState(focusReleaseId)
  const [showBothPass, setShowBothPass] = useState(false)
  const [expandedCase, setExpandedCase] = useState('')

  const baselineRelease = selectable.find(item => item.release_id === baselineId)
  const candidateRelease = selectable.find(item => item.release_id === candidateId)

  const baselineQuery = useQuery({
    queryKey: ['admin', 'evaluation-run-detail', baselineRelease?.eval_run_id],
    queryFn: () => evaluationService.getRun(baselineRelease!.eval_run_id!),
    enabled: Boolean(baselineRelease?.eval_run_id),
  })
  const candidateQuery = useQuery({
    queryKey: ['admin', 'evaluation-run-detail', candidateRelease?.eval_run_id],
    queryFn: () => evaluationService.getRun(candidateRelease!.eval_run_id!),
    enabled: Boolean(candidateRelease?.eval_run_id),
  })

  const baselineDetail = baselineQuery.data
  const candidateDetail = candidateQuery.data
  const diffRows = useMemo(
    () => baselineDetail && candidateDetail
      ? buildCaseDiff(baselineDetail.report.results ?? [], candidateDetail.report.results ?? [])
      : [],
    [baselineDetail, candidateDetail],
  )
  const diffCounts = useMemo(() => {
    const counts = {} as Record<CaseDiffKind, number>
    for (const row of diffRows) counts[row.kind] = (counts[row.kind] ?? 0) + 1
    return counts
  }, [diffRows])
  const visibleRows = showBothPass ? diffRows : diffRows.filter(row => row.kind !== 'both_pass')

  const releaseOptionLabel = (item: PromptReleaseRecord) =>
    `v${item.version} · ${item.status} · ${timeShort(item.created_at || item.updated_at || null)}`
  const bothLoaded = Boolean(baselineDetail && candidateDetail)
  const tierRows = useMemo(() => {
    const baselineTiers = baselineDetail?.report.tier_summaries ?? []
    const candidateTiers = candidateDetail?.report.tier_summaries ?? []
    return Array.from(new Set([...baselineTiers.map(item => item.tier), ...candidateTiers.map(item => item.tier)])).sort()
  }, [baselineDetail, candidateDetail])

  return <Drawer open title="发布评测对比" subtitle="同 Prompt 两次评测的汇总与逐用例差异；数据读脱敏报告，纯前端对齐。" onClose={onClose} width="max-w-5xl">
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {[{ label: '基准', value: baselineId, set: setBaselineId }, { label: '对比', value: candidateId, set: setCandidateId }].map(slot => (
          <label key={slot.label} className="block text-xs text-text-secondary">
            {slot.label}运行
            <select aria-label={`${slot.label}运行`} value={slot.value} onChange={e => { slot.set(e.target.value); setExpandedCase('') }} className={`${ctl} mt-1 w-full`}>
              <option value="">选择记录</option>
              {selectable.map(item => <option key={item.release_id} value={item.release_id}>{releaseOptionLabel(item)}</option>)}
            </select>
          </label>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <ReleaseRunSummary label="基准" detail={baselineDetail} loading={baselineQuery.isPending} />
        <ReleaseRunSummary label="对比" detail={candidateDetail} loading={candidateQuery.isPending} />
      </div>

      {(baselineQuery.isError || candidateQuery.isError) && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">
        {baselineQuery.isError ? errMessage(baselineQuery.error) : ''}{candidateQuery.isError ? ` ${errMessage(candidateQuery.error)}` : ''}
      </p>}

      {bothLoaded && tierRows.length > 0 && <TableWrap minWidth={520}>
        <thead className="bg-bg-elevated"><tr><Th>分层</Th><Th end>基准通过</Th><Th end>基准阈值</Th><Th end>对比通过</Th><Th end>对比阈值</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {tierRows.map(tier => {
            const b = baselineDetail?.report.tier_summaries.find(item => item.tier === tier)
            const c = candidateDetail?.report.tier_summaries.find(item => item.tier === tier)
            return <tr key={tier}>
              <Td>{tier}</Td>
              <Td end>{b ? `${(b.pass_rate * 100).toFixed(0)}%（${b.passed}/${b.total}）` : '—'}</Td>
              <Td end>{b ? `${(b.threshold * 100).toFixed(0)}%` : '—'}</Td>
              <Td end>{c ? `${(c.pass_rate * 100).toFixed(0)}%（${c.passed}/${c.total}）` : '—'}</Td>
              <Td end>{c ? `${(c.threshold * 100).toFixed(0)}%` : '—'}</Td>
            </tr>
          })}
        </tbody>
      </TableWrap>}

      {bothLoaded && <>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
          {(['regressed', 'improved', 'both_fail', 'baseline_only', 'candidate_only'] as CaseDiffKind[])
            .filter(kind => diffCounts[kind])
            .map(kind => <Badge key={kind} text={`${CASE_DIFF_KIND_LABELS[kind]} ${diffCounts[kind]}`} tone={kind === 'regressed' ? 'bad' : kind === 'improved' ? 'good' : 'gray'} />)}
          <label className="ml-auto flex items-center gap-1.5 text-text-secondary">
            <input type="checkbox" checked={showBothPass} onChange={e => setShowBothPass(e.target.checked)} />
            显示两侧通过
          </label>
        </div>
        {visibleRows.length === 0
          ? <Empty text={diffRows.length === 0 ? '两侧报告都没有逐题结果。' : '无差异化用例（全部同态）。'} />
          : <TableWrap minWidth={860}>
            <thead className="bg-bg-elevated"><tr><Th>用例</Th><Th>归类</Th><Th>基准状态 / 说明</Th><Th>对比状态 / 说明</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {visibleRows.map(row => {
                const kindTone = row.kind === 'regressed' ? 'bad' : row.kind === 'improved' ? 'good' : row.kind === 'both_pass' ? 'gray' : 'warn'
                return <FragmentRow
                  key={row.caseId}
                  row={row}
                  kindTone={kindTone}
                  expanded={expandedCase === row.caseId}
                  onToggle={() => setExpandedCase(current => current === row.caseId ? '' : row.caseId)}
                />
              })}
            </tbody>
          </TableWrap>}
        <p className="text-[11px] text-text-muted">对比仅在相同题集口径的运行间有意义；两侧题集不同会先表现为大量"仅单侧"用例。</p>
      </>}
    </div>
  </Drawer>
}

function FragmentRow({ row, kindTone, expanded, onToggle }: {
  row: CaseDiffRow
  kindTone: 'bad' | 'good' | 'warn' | 'gray'
  expanded: boolean
  onToggle: () => void
}) {
  const sideCell = (result: EvalResultDetail | null) => result
    ? <span className="block">
      <Badge text={result.status} tone={result.status === 'pass' ? 'good' : result.status === 'fail' ? 'bad' : 'warn'} />
      <span className="ml-1.5 break-words text-[11px] text-text-secondary">{result.error_msg || '—'}</span>
    </span>
    : <span className="text-text-muted">（该侧无此用例）</span>
  return <>
    <tr className={expanded ? 'bg-bg-hover' : 'cursor-pointer'} onClick={onToggle}>
      <Td mono>{row.caseId}</Td>
      <Td><Badge text={CASE_DIFF_KIND_LABELS[row.kind]} tone={kindTone} /></Td>
      <Td>{sideCell(row.baseline)}</Td>
      <Td>{sideCell(row.candidate)}</Td>
    </tr>
    {expanded && row.baseline && row.candidate && <tr><td colSpan={4} className="bg-bg-elevated/50 p-3">
      <div className="grid gap-3 lg:grid-cols-2">
        {(['baseline', 'candidate'] as const).map(side => {
          const result = side === 'baseline' ? row.baseline! : row.candidate!
          return <div key={side}>
            <p className="mb-1 text-[11px] font-medium text-text-secondary">{side === 'baseline' ? '基准侧' : '对比侧'} · 失败阶段 {result.error_stage || '—'} · 耗时 {result.duration_ms || '—'}ms</p>
            <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded bg-black/[0.03] p-2 text-[10px] font-mono text-text-primary">{JSON.stringify(result.actual ?? {}, null, 2)}</pre>
          </div>
        })}
      </div>
    </td></tr>}
  </>
}
