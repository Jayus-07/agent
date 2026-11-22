'use client'

/**
 * 运行评测：Prompt 发布评测发起 + 分页历史分开；历史记录进入运行详情页后逐题查看脱敏结果。
 *
 * 批次 1（2026-11-22 评测中心改造）：
 * - 发起评测只保留「Prompt 发布评测」一条通道（复用发布门禁链路）；
 *   数据集方案（V2）发起入口冻结移除，历史查看与 Gate 审批不受影响。
 * - 域导航（?domain=）传入后：发起表单按域过滤/分组 Prompt，运行记录
 *   三来源按域归堆并展示域徽标。
 * - 评测方式按被测域诚实展示：RAG 四选真实生效；SQL 由 canonical 双跑
 *   链路决定；其余域走离线契约并回落 RAG 门禁口径留痕。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, Play, RotateCw, X } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  evaluationService,
  type EvaluationV2Gate,
  type EvaluationV2Run,
} from '@/api/evaluation'
import { createIdempotencyKey, errMessage } from '@/api/client'
import { promptsService, type PromptListItem, type PromptReleaseRecord } from '@/api/prompts'
import { PROMPT_RELEASE_SUITE } from '@/config/promptRelease'
import { atLeast } from '@/lib/auth'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Empty, modeShort, MODE_LABELS, StatusBadge, TableWrap, Td, Th, timeShort, fmtCost } from './evalUi'
import {
  EVAL_DOMAINS,
  domainLabel,
  moduleToDomain,
  promptKeyToDomain,
  type DomainFilter,
} from '../domains'
import RunHistoryDetail from './RunHistoryDetail'
import EvalRunDetailPanel from './EvalRunDetailPanel'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked'])
const RUN_PAGE_SIZE = 10
const RELEASE_TERMINAL = new Set(['failed', 'passed', 'approved', 'published', 'rolled_back'])

type RunPane = 'launch' | 'history'
// 2026-11-22：评测中心是评测的统一入口——运行记录聚合三类来源
// （V2 方案 / Prompt 发布评测 / CLI 台账），域过滤由 EvaluationCenter 传入。
type HistorySource = 'v2' | 'release' | 'ledger'

/** SQL 发布评测的套件标记：canonical 内置集，runner 不依赖 suite 参数（仅记录可读性）。 */
const SQL_RELEASE_SUITE = 'sql_canonical'

export default function RunsTab({ promptPrefill, focusRunId, domain = 'all', onGoCompare }: { promptPrefill?: { key: string; version: string } | null; focusRunId?: string | null; domain?: DomainFilter; onGoCompare?: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const allowed = atLeast('super_admin')
  const [runPane, setRunPane] = useState<RunPane>('launch')
  const [historyPage, setHistoryPage] = useState(0)
  const [historySource, setHistorySource] = useState<HistorySource>('v2')
  const [relKey, setRelKey] = useState('')
  const [relVersion, setRelVersion] = useState('')
  const [relMode, setRelMode] = useState('self+ragas')
  const [expandedReleaseId, setExpandedReleaseId] = useState('')
  const [detailRunId, setDetailRunId] = useState('')
  // 发布评测 / CLI 台账的逐题详情（读报告文件口径，与 V2 详情互斥）
  const [detailEvalRun, setDetailEvalRun] = useState('')
  const [gateRunId, setGateRunId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const optionsQuery = useQuery({
    queryKey: ['admin', 'evaluation-center-run-options'],
    enabled: allowed,
    queryFn: async () => {
      const promptItems = await promptsService.list().catch(() => [] as PromptListItem[])
      return { promptKeys: promptItems }
    },
  })
  const promptKeys: PromptListItem[] = optionsQuery.data?.promptKeys ?? []
  // 域过滤（批次 1）：发起表单按 prompt_key 前缀；运行记录三来源按各自归域规则
  const domainPromptKeys = useMemo(
    () => domain === 'all' ? promptKeys : promptKeys.filter(item => promptKeyToDomain(item.key) === domain),
    [promptKeys, domain],
  )
  const runsQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-runs', historyPage],
    enabled: allowed && runPane === 'history' && !detailRunId,
    queryFn: async () => {
      const rows = await evaluationService.listV2Runs(RUN_PAGE_SIZE + 1, historyPage * RUN_PAGE_SIZE)
      return { rows: rows.slice(0, RUN_PAGE_SIZE), hasNext: rows.length > RUN_PAGE_SIZE }
    },
    refetchInterval: query => query.state.data?.rows.some(run => !TERMINAL.has(run.job_status)) ? 3000 : false,
  })
  const runs = useMemo(() => {
    const rows = runsQuery.data?.rows ?? []
    if (domain === 'all') return rows
    return rows.filter(row => {
      const firstPrompt = Object.keys(row.prompt_versions || {})[0]
      const byPrompt = promptKeyToDomain(firstPrompt)
      if (firstPrompt && byPrompt !== 'agent') return byPrompt === domain
      return moduleToDomain(row.module) === domain
    })
  }, [runsQuery.data, domain])

  // 发布门禁评测记录（含 SQL 双跑等，2026-11-22 聚合进评测中心）
  const releasesQuery = useQuery({
    queryKey: ['admin', 'prompt-release-runs'],
    enabled: allowed && runPane === 'history' && historySource === 'release' && !detailRunId,
    queryFn: () => promptsService.listAllReleases(50),
    refetchInterval: query => query.state.data?.some(item => !RELEASE_TERMINAL.has(item.status)) ? 4000 : false,
  })
  const releases = useMemo(
    () => domain === 'all' ? (releasesQuery.data ?? []) : (releasesQuery.data ?? []).filter(row => promptKeyToDomain(row.prompt_key) === domain),
    [releasesQuery.data, domain],
  )

  // CLI 台账（ai.eval_run_records：CLI / 线上采样 / 历史口径评测）
  const ledgerQuery = useQuery({
    queryKey: ['admin', 'evaluation-ledger-runs'],
    enabled: allowed && runPane === 'history' && historySource === 'ledger' && !detailRunId,
    queryFn: () => evaluationService.listRuns(50),
  })
  const ledgerRuns = useMemo(
    () => domain === 'all' ? (ledgerQuery.data ?? []) : (ledgerQuery.data ?? []).filter(row => moduleToDomain(row.module) === domain),
    [ledgerQuery.data, domain],
  )

  useEffect(() => {
    if (!promptPrefill) return
    const version = promptPrefill.version.trim()
    if (!/^\d+$/.test(version)) return
    setRelKey(promptPrefill.key)
    setRelVersion(version)
  }, [promptPrefill])
  useEffect(() => {
    if (!focusRunId) return
    setDetailRunId(focusRunId)
    setRunPane('history')
  }, [focusRunId])

  const relDomain = promptKeyToDomain(relKey)
  const relLatest = promptKeys.find(item => item.key === relKey)?.latest_version_number ?? null

  // 2026-11-22：从评测中心发起 Prompt 发布评测（复用发布门禁链路：候选
  // 版本经评测与审批后才能发布；SQL 类 prompt 在该链路上跑真实生成评测）。
  // suite 按域标记：RAG 用真实存在的发布门禁 smoke 集；SQL 用 canonical
  // 内置集标记；其余类别回落 eval_center 元数据标记。
  const suiteForRelease = (key: string) => {
    const keyDomain = promptKeyToDomain(key)
    if (keyDomain === 'rag') return PROMPT_RELEASE_SUITE
    if (keyDomain === 'sql') return SQL_RELEASE_SUITE
    return 'eval_center'
  }
  const startRelease = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!relKey.trim()) throw new Error('请选择要评测的 Prompt。')
      if (!/^\d+$/.test(relVersion.trim())) throw new Error('请填写要评测的候选版本号（正整数）。')
      // 评测方式按被测域诚实提交：RAG 四选真实生效；其余域固定 offline
      // 离线契约（表单上不出现方式选择，避免"显示 A 提交 B"的静默覆盖）。
      const record = await promptsService.createRelease(relKey.trim(), Number(relVersion.trim()), {
        suite: suiteForRelease(relKey.trim()),
        evaluation_mode: (relDomain === 'rag' ? relMode : 'offline') as 'offline' | 'semantic' | 'ragas' | 'self+ragas',
        executor: 'local',
        dataset_version: { source: 'eval-center', evaluation_mode: relDomain === 'rag' ? relMode : 'offline' },
      })
      setMessage(`发布评测已入队：${record.release_id.slice(0, 12)}（候选 ${relKey}@v${relVersion}）。已切到“运行记录 → 发布评测”跟踪进度。`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'prompt-release-runs'] })
      setRunPane('history')
      setHistorySource('release')
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '发起发布评测失败') }
    finally { setBusy(false) }
  }
  const cancel = async (run: EvaluationV2Run) => {
    setBusy(true); setError('')
    try {
      const response = await evaluationService.cancelV2Run(run.run_id)
      setMessage(response.accepted ? `已发送取消请求：${run.run_id.slice(0, 8)}` : '运行已进入终态')
      await queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '取消失败') }
    finally { setBusy(false) }
  }
  const retry = async (run: EvaluationV2Run) => {
    setBusy(true); setError('')
    try {
      await evaluationService.retryV2Run(run.run_id)
      setMessage(`已请求从冻结快照恢复：${run.run_id.slice(0, 8)}`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '重试失败') }
    finally { setBusy(false) }
  }

  if (!allowed) return <p className="p-5 text-sm text-text-secondary">评测运行沿用任务授权，仅超级管理员可发起和查看。</p>

  const queryError = optionsQuery.isError ? errMessage(optionsQuery.error)
    : runsQuery.isError ? errMessage(runsQuery.error) : ''

  return <div className="space-y-4">
    {queryError && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{queryError}</p>}
    {error && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}
    {message && <p role="status" className="rounded-md bg-bg-elevated px-3 py-2 text-xs text-text-secondary">{message}</p>}

    <div className="flex border-b border-border-subtle" role="tablist" aria-label="运行评测">
      <button type="button" role="tab" aria-selected={runPane === 'launch'} aria-controls="evaluation-run-launch" onClick={() => setRunPane('launch')} className={`-mb-px border-b-2 px-4 py-2.5 text-xs font-medium ${runPane === 'launch' ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
        发起评测
      </button>
      <button type="button" role="tab" aria-selected={runPane === 'history'} aria-controls="evaluation-run-history" onClick={() => { setDetailRunId(''); setRunPane('history') }} className={`-mb-px border-b-2 px-4 py-2.5 text-xs font-medium ${runPane === 'history' ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
        运行记录
      </button>
    </div>

    {runPane === 'launch' ? <section id="evaluation-run-launch" role="tabpanel" aria-label="发起评测" className="space-y-4">
      <Card title="发起评测" subtitle="对候选 Prompt 版本执行发布门禁评测；运行固定数据集版本与模型 / Prompt / 知识库快照，两次运行在相同口径下才可直接对比。">
        <p className="mb-3 text-xs text-text-secondary">
          对候选 Prompt 版本执行发布门禁评测（复用发布链路；SQL 类 Prompt 在此链路做真实生成评测）。
          评测通过后到「Prompt CI/CD」对应 Prompt 页完成审批与发布。
        </p>
        <div className="grid gap-3 lg:grid-cols-[1.4fr_auto_auto_auto]">
          <select aria-label="发布评测 Prompt" value={relKey} onChange={e => setRelKey(e.target.value)} className={ctl}>
            <option value="">选择 Prompt</option>
            {domain === 'all'
              ? EVAL_DOMAINS.map(item => {
                const group = domainPromptKeys.filter(prompt => promptKeyToDomain(prompt.key) === item.key)
                if (!group.length) return null
                return <optgroup key={item.key} label={domainLabel(item.key)}>
                  {group.map(prompt => <option key={prompt.key} value={prompt.key}>{prompt.key} · {prompt.name}</option>)}
                </optgroup>
              })
              : domainPromptKeys.map(prompt => <option key={prompt.key} value={prompt.key}>{prompt.key} · {prompt.name}</option>)}
          </select>
          <input
            aria-label="发布评测候选版本" type="number" min={1}
            placeholder={relLatest ? `现网 v${relLatest}` : '候选版本'}
            title={relLatest ? `当前已发布 v${relLatest}，评测通过后候选版本将接替该版本` : undefined}
            value={relVersion} onChange={e => setRelVersion(e.target.value)} className={`${ctl} w-28`}
          />
          {relDomain === 'rag'
            ? <select aria-label="发布评测方式" value={relMode} onChange={e => setRelMode(e.target.value)} className={ctl}>
              {Object.entries(MODE_LABELS).map(([value, labelText]) => <option key={value} value={value}>{labelText}</option>)}
            </select>
            : <p className="self-center text-xs text-text-muted" title="评测方式由该域发布链路决定，无需选择">
              {relDomain === 'sql' ? 'SQL 走 canonical 双跑对比门（方式由链路决定）' : '离线契约评测（该域专属模块建设中，回落 RAG 门禁口径留痕）'}
            </p>}
          <button type="button" className={ctlPrimary} disabled={busy || !relKey} onClick={() => void startRelease()}><Play size={13} className="mr-1 inline" aria-hidden="true" />开始评测</button>
        </div>
        <p className="mt-3 text-[11px] text-text-muted">
          SQL 类 Prompt：门禁按 canonical 15 条题集做「候选 vs 现网」双跑对比（候选不比现网差才放行）。
          RAG 类 Prompt：门禁按 desktop_upload_smoke 8 条短集跑分层阈值门（smoke ≥95%）。
          其他类别暂无专属评测模块，将回落 RAG 门禁口径并留痕。
        </p>
      </Card>
    </section> : <section id="evaluation-run-history" role="tabpanel" aria-label="运行记录" className="space-y-4">
      {detailRunId ? <RunHistoryDetail key={detailRunId} runId={detailRunId} onBack={() => setDetailRunId('')} onGoCompare={onGoCompare} /> : detailEvalRun ? <EvalRunDetailPanel key={detailEvalRun} runId={detailEvalRun} onClose={() => setDetailEvalRun('')} /> : <Card
        title="运行记录"
        subtitle="按创建时间倒序；覆盖 V2 方案评测、Prompt 发布评测与 CLI 台账三类来源。"
        actions={<button type="button" className={ctlGhost} disabled={(historySource === 'v2' && runsQuery.isFetching) || (historySource === 'release' && releasesQuery.isFetching) || (historySource === 'ledger' && ledgerQuery.isFetching)} onClick={() => { if (historySource === 'v2') void runsQuery.refetch(); else if (historySource === 'release') void releasesQuery.refetch(); else void ledgerQuery.refetch() }}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />刷新</button>}
      >
        <div className="mb-4 flex gap-1 border-b border-border-subtle">
          {([['v2', 'V2 方案评测'], ['release', '发布评测'], ['ledger', 'CLI 台账']] as const).map(([value, labelText]) => <button key={value} type="button" role="tab" aria-selected={historySource === value} onClick={() => setHistorySource(value)} className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium ${historySource === value ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
            {labelText}
          </button>)}
        </div>
        {historySource === 'release' ? <ReleaseRunsTable
          loading={releasesQuery.isPending}
          error={releasesQuery.isError ? errMessage(releasesQuery.error) : ''}
          releases={releases}
          expandedId={expandedReleaseId}
          onToggle={id => setExpandedReleaseId(current => current === id ? '' : id)}
          onOpenRunDetail={runId => setDetailEvalRun(runId)}
          onRetryRefresh={() => void releasesQuery.refetch()}
        /> : historySource === 'ledger' ? <LedgerRunsTable
          loading={ledgerQuery.isPending}
          error={ledgerQuery.isError ? errMessage(ledgerQuery.error) : ''}
          runs={ledgerRuns}
          onOpenRunDetail={runId => setDetailEvalRun(runId)}
        /> : <>
        {runsQuery.isPending ? <Empty text="正在读取运行记录…" /> : runsQuery.isError ? <div className="space-y-2 py-5 text-center">
          <p role="alert" className="text-xs text-red-700">{errMessage(runsQuery.error)}</p>
          <button type="button" className={ctl} onClick={() => void runsQuery.refetch()}>重新读取</button>
        </div> : runs.length === 0 ? <Empty text={historyPage === 0 ? '还没有运行记录。切到“发起评测”开始第一次运行。' : '这一页没有运行记录。'} /> : <>
          <TableWrap minWidth={1160}>
            <thead className="bg-bg-elevated"><tr><Th>Run</Th><Th>域</Th><Th>数据集</Th><Th>被测配置</Th><Th>评测方式</Th><Th>状态</Th><Th>Gate</Th><Th>进度 / 结果</Th><Th end>费用</Th><Th end>耗时</Th><Th>完成时间</Th><Th end>操作</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {runs.map(run => <Fragment key={run.run_id}><tr>
                <Td mono>{run.run_id.slice(0, 8)}</Td>
                <Td><Badge text={domainLabel(moduleToDomain(run.module))} tone="blue" /></Td>
                <Td><span className="block text-text-primary">{run.suite_name || run.suite_key}</span><span className="text-[11px] text-text-muted">v{run.suite_version || '—'}</span></Td>
                <Td>{(() => { const versions = run.prompt_versions || {}; return Object.keys(versions).length ? Object.entries(versions).map(([key, value]) => `${key}@${String(value)}`).join(' · ') : '默认配置' })()}</Td>
                <Td>{modeShort(run.evaluation_mode)}{run.source && run.source !== 'manual' && <span className="ml-1.5"><Badge text={run.source === 'online' ? '线上采样' : run.source === 'prompt_release' ? '发布' : run.source} tone="accent" /></span>}</Td>
                <Td><StatusBadge status={run.job_status} /></Td>
                <Td>
                  {run.gate_verdict ? <Badge text={run.gate_verdict} tone={run.gate_verdict === 'PASS' ? 'good' : run.gate_verdict === 'FAIL' ? 'bad' : 'warn'} /> : <span className="text-text-muted">—</span>}
                  {!!run.gate_reason?.length && <details className="mt-1 max-w-48 text-[11px] text-text-muted">
                    <summary className="cursor-pointer">裁决原因（{run.gate_reason.length}）</summary>
                    <ul className="mt-1 space-y-1">{run.gate_reason.map((reason, index) => <li key={index} className="break-words">{JSON.stringify(reason)}</li>)}</ul>
                  </details>}
                </Td>
                <Td>{run.completed_cases}/{run.total_cases} · 通过 {run.passed_cases}</Td>
                <Td end>{fmtCost(run.cost_amount, run.cost_currency)}</Td>
                <Td end>{run.started_at && run.finished_at ? `${Math.max(1, Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000))}s` : '—'}</Td>
                <Td>{timeShort(run.finished_at)}</Td>
                <Td end>
                  <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setDetailRunId(run.run_id)}>运行详情</button>
                  {run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setGateRunId(current => current === run.run_id ? '' : run.run_id)}>{run.gate_verdict === 'PASS' ? 'Gate 审批' : 'Gate 详情'}</button>}
                  {onGoCompare && run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => onGoCompare(run.run_id)}>对比<ArrowRight size={11} className="ml-0.5 inline" aria-hidden="true" /></button>}
                  {!TERMINAL.has(run.job_status) && <button type="button" className={`${ctlGhost} mr-1`} disabled={busy} onClick={() => void cancel(run)}><X size={12} className="mr-0.5 inline" aria-hidden="true" />取消</button>}
                  {run.job_status === 'failed' && <button type="button" className={ctl} disabled={busy} onClick={() => void retry(run)}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />重试</button>}
                </Td>
              </tr>
              {gateRunId === run.run_id && <tr><td colSpan={12} className="bg-bg-elevated/50 p-3"><GateApprovalPanel runId={run.run_id} onClose={() => setGateRunId('')} onSubmitted={() => void queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })} /></td></tr>}
              </Fragment>)}
            </tbody>
          </TableWrap>
          <div className="mt-3 flex items-center justify-end gap-2 border-t border-border-subtle pt-3">
            <button type="button" className={ctl} disabled={historyPage === 0 || runsQuery.isFetching} onClick={() => setHistoryPage(current => Math.max(0, current - 1))}>上一页</button>
            <span className="min-w-16 text-center text-[11px] text-text-muted">第 {historyPage + 1} 页</span>
            <button type="button" className={ctl} disabled={!runsQuery.data?.hasNext || runsQuery.isFetching} onClick={() => setHistoryPage(current => current + 1)}>下一页</button>
          </div>
        </>}
        </>}
      </Card>}
    </section>}
  </div>
}

// ── 发布评测表格（Prompt 发布门禁记录，含 SQL 双跑）──────────────

/**
 * 门禁字段人类可读化：ragas_applicable=false 是「未接入」而非「未通过」；
 * candidate_regression 对象展开为对比摘要；字符串去 JSON 引号。
 */
function formatGateValue(key: string, value: unknown): string {
  if (typeof value === 'boolean') {
    if (key === 'ragas_applicable' && !value) return '不适用（未接入 RAGAS）'
    return value ? '通过' : '未通过'
  }
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const regression = value as { threshold?: number; current_pass_rate?: number; candidate_pass_rate?: number; regression_pass?: boolean }
    if (key === 'candidate_regression' && typeof regression.regression_pass === 'boolean') {
      const pct = (rate?: number) => typeof rate === 'number' ? `${(rate * 100).toFixed(1)}%` : '—'
      return `候选 ${pct(regression.candidate_pass_rate)} vs 现网 ${pct(regression.current_pass_rate)}，阈值 ${regression.threshold ?? '—'}，${regression.regression_pass ? '回归通过' : '回归未通过'}`
    }
    return JSON.stringify(value)
  }
  return String(value)
}

function ReleaseRunsTable({ loading, error, releases, expandedId, onToggle, onOpenRunDetail, onRetryRefresh }: {
  loading: boolean
  error: string
  releases: PromptReleaseRecord[]
  expandedId: string
  onToggle: (id: string) => void
  onOpenRunDetail: (runId: string) => void
  onRetryRefresh: () => void
}) {
  const releaseRate = (record: PromptReleaseRecord, field: 'candidate_pass_rate' | 'current_pass_rate') => {
    const value = (record.metrics as Record<string, unknown> | undefined)?.[field]
    return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : null
  }
  const releaseGate = (record: PromptReleaseRecord) => {
    const gate = (record.metrics as { gate?: { blocked_rules?: Array<{ rule?: string; message?: string }> } } | undefined)?.gate
    return gate?.blocked_rules ?? []
  }
  if (loading) return <Empty text="正在读取发布评测记录…" />
  if (error) return <div className="space-y-2 py-5 text-center">
    <p role="alert" className="text-xs text-red-700">{error}</p>
    <button type="button" className={ctl} onClick={onRetryRefresh}>重新读取</button>
  </div>
  if (releases.length === 0) return <Empty text="该域暂无发布评测记录。切到“发起评测”发起第一次。" />
  return <TableWrap minWidth={1100}>
    <thead className="bg-bg-elevated"><tr><Th>Release</Th><Th>域</Th><Th>候选 Prompt</Th><Th>方式</Th><Th>状态</Th><Th>候选 vs 现网</Th><Th>门禁</Th><Th>发起人</Th><Th>时间</Th><Th end>操作</Th></tr></thead>
    <tbody className="divide-y divide-border-subtle">
      {releases.map(record => {
        const blocked = releaseGate(record)
        const evalRunId = record.eval_run_id
        const statusHint = record.failure_reason
          || (evalRunId?.startsWith('local-') ? 'CLI 本地执行失败（非门禁拦截）' : '')
        return (<Fragment key={record.release_id}><tr>
          <Td mono>{record.release_id.slice(0, 12)}</Td>
          <Td><Badge text={domainLabel(promptKeyToDomain(record.prompt_key))} tone="blue" /></Td>
          <Td><span className="block text-text-primary">{record.prompt_key}</span><span className="text-[11px] text-text-muted">v{record.version} · {record.eval_suite}</span></Td>
          <Td>{record.executor === 'local' ? '本机执行' : 'GitHub 执行'}</Td>
          <Td>
            <span title={statusHint || undefined}>
              <StatusBadge status={record.status} />
            </span>
          </Td>
          <Td>{(() => {
            const candidate = releaseRate(record, 'candidate_pass_rate')
            const current = releaseRate(record, 'current_pass_rate')
            if (!candidate && !current) return <span className="text-text-muted">—</span>
            return <span className="text-xs">候选 {candidate ?? '—'} · 现网 {current ?? '—'}</span>
          })()}</Td>
          <Td>
            {record.status === 'passed' || record.status === 'approved' || record.status === 'published'
              ? <Badge text="门禁通过" tone="good" />
              : record.status === 'failed'
                ? <Badge text={blocked.length ? `拦截 ${blocked.length} 项` : '失败'} tone="bad" />
                : <Badge text="评测中" tone="warn" />}
          </Td>
          <Td>{record.created_by || '—'}</Td>
          <Td>{timeShort(record.created_at || record.updated_at || null)}</Td>
          <Td end>
            <button type="button" className={ctlGhost} onClick={() => onToggle(record.release_id)}>{expandedId === record.release_id ? '收起' : '门禁详情'}</button>
            {evalRunId ? <button type="button" className={ctlGhost} onClick={() => onOpenRunDetail(evalRunId)}>逐题详情</button> : null}
          </Td>
        </tr>
        {expandedId === record.release_id && <tr><td colSpan={10} className="bg-bg-elevated/50 p-3">
          <div className="grid gap-3 text-[11px] lg:grid-cols-2">
            <div>
              <p className="mb-1 font-medium text-text-secondary">门禁判定（metrics.gate）</p>
              {(() => {
                const gate = (record.metrics as { gate?: Record<string, unknown> } | undefined)?.gate
                if (!gate) return <p className="text-text-muted">评测未产出门禁数据（pending/running 或旧记录）。</p>
                return <ul className="space-y-1 text-text-secondary">
                  {Object.entries(gate).filter(([key]) => !['blocked_rules', 'tiers', 'regression', 'ragas', 'sample_gate'].includes(key)).map(([key, value]) => (
                    <li key={key}><span className="text-text-muted">{key}:</span> {formatGateValue(key, value)}</li>
                  ))}
                </ul>
              })()}
            </div>
            <div>
              <p className="mb-1 font-medium text-text-secondary">拦截规则（blocked_rules）</p>
              {blocked.length === 0 ? <p className="text-text-muted">无拦截项。</p> : (
                <ul className="space-y-1">
                  {blocked.map((item, index) => <li key={index} className="break-words text-red-700">[{item.rule || '?'}] {typeof item.message === 'string' ? item.message : JSON.stringify(item)}</li>)}
                </ul>
              )}
              {!!record.failure_reason && <p className="mt-2 break-words text-text-secondary"><span className="text-text-muted">失败原因：</span>{record.failure_reason}</p>}
            </div>
          </div>
        </td></tr>}
        </Fragment>)
      })}
    </tbody>
  </TableWrap>
}

// ── CLI 台账表格（ai.eval_run_records）────────────────────────

function LedgerRunsTable({ loading, error, runs, onOpenRunDetail }: {
  loading: boolean
  error: string
  runs: Awaited<ReturnType<typeof evaluationService.listRuns>>
  onOpenRunDetail: (runId: string) => void
}) {
  if (loading) return <Empty text="正在读取 CLI 台账…" />
  if (error) return <div className="space-y-2 py-5 text-center">
    <p role="alert" className="text-xs text-red-700">{error}</p>
  </div>
  if (runs.length === 0) return <Empty text="该域暂无台账记录（CLI 评测与线上采样写入此台账）。" />
  return <TableWrap minWidth={960}>
    <thead className="bg-bg-elevated"><tr><Th>Run</Th><Th>域</Th><Th>模块</Th><Th>状态</Th><Th>通过率</Th><Th>样本</Th><Th>数据集版本</Th><Th>触发</Th><Th>时间</Th><Th end>操作</Th></tr></thead>
    <tbody className="divide-y divide-border-subtle">
      {runs.map(run => <tr key={run.run_id}>
        <Td mono>{run.run_id.slice(0, 16)}</Td>
        <Td><Badge text={domainLabel(moduleToDomain(run.module))} tone="blue" /></Td>
        <Td>{run.module}</Td>
        <Td>{run.status ? <StatusBadge status={run.status} /> : <span className="text-text-muted">—</span>}</Td>
        <Td>{(run.pass_rate * 100).toFixed(1)}%</Td>
        <Td>{run.case_count ?? '—'}</Td>
        <Td>{run.dataset_version || '—'}</Td>
        <Td>{run.trigger || run.triggered_by || '—'}</Td>
        <Td>{timeShort(run.timestamp)}</Td>
        <Td end><button type="button" className={ctlGhost} onClick={() => onOpenRunDetail(run.run_id)}>评测详情</button></Td>
      </tr>)}
    </tbody>
  </TableWrap>
}
function GateApprovalPanel({ runId, onClose, onSubmitted }: { runId: string; onClose: () => void; onSubmitted: () => void }) {
  const [gate, setGate] = useState<EvaluationV2Gate | null>(null)
  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [releaseReference, setReleaseReference] = useState('')
  const [rationale, setRationale] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const pendingRequest = useRef<{ key: string; requestId: string } | null>(null)

  const loadGate = useCallback(async () => {
    setLoading(true); setError('')
    try { setGate(await evaluationService.getV2Gate(runId)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '读取 Gate 记录失败') }
    finally { setLoading(false) }
  }, [runId])
  useEffect(() => { void loadGate() }, [loadGate])

  const latest = gate?.decisions[0]
  const canReview = gate?.verdict === 'PASS' && latest?.verdict === 'PASS'
  const referenceValid = /^[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}$/.test(releaseReference.trim())
  const rationaleValid = rationale.trim().length >= 8 && rationale.trim().length <= 2000

  const submit = async () => {
    if (!canReview || !latest) { setError('最新 Gate 不是 PASS，请刷新后核对。'); return }
    const cleanReference = releaseReference.trim()
    const cleanRationale = rationale.trim()
    if (!referenceValid || !rationaleValid) { setError('请填写有效的发布引用和 8 到 2000 字审批理由。'); return }
    const key = JSON.stringify([latest.decision_id, decision, cleanReference, cleanRationale])
    const requestId = pendingRequest.current?.key === key ? pendingRequest.current.requestId : createIdempotencyKey()
    pendingRequest.current = { key, requestId }
    setSaving(true); setError(''); setMessage('')
    try {
      const approval = await evaluationService.createV2GateApproval(runId, {
        gate_decision_id: latest.decision_id,
        request_id: requestId,
        decision,
        release_reference: cleanReference,
        rationale: cleanRationale,
      })
      pendingRequest.current = null
      setMessage(`Gate 发布意见已记录：${approval.decision === 'approved' ? '批准' : '拒绝'} · ${approval.release_reference}`)
      await loadGate()
      onSubmitted()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '提交 Gate 审批失败') }
    finally { setSaving(false) }
  }

  return <section className="space-y-3 rounded-md border border-border-subtle bg-surface-base p-3" aria-label={`Run ${runId} Gate 审批`}>
    <div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-medium text-text-primary">Gate 审批 · {runId.slice(0, 8)}</h3><p className="text-xs text-text-muted">只允许针对最新 PASS 裁决记录批准或拒绝意见。</p></div><button type="button" className={ctlGhost} onClick={onClose}>收起</button></div>
    {loading && <p role="status" className="text-xs text-text-secondary">正在读取 Gate 裁决与审批历史…</p>}
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {message && <p role="status" className="text-xs text-text-secondary">{message}</p>}
    {gate && <>
      <div className="space-y-1 text-xs text-text-secondary"><p>当前状态：<Badge text={gate.verdict || '待裁决'} tone={gate.verdict === 'PASS' ? 'good' : gate.verdict === 'FAIL' ? 'bad' : 'warn'} /></p>
        {gate.reasons.length > 0 && <ul className="list-disc pl-5">{gate.reasons.map((reason, index) => <li key={index}>{JSON.stringify(reason)}</li>)}</ul>}
        {gate.approvals.length > 0 && <p>最近意见：{gate.approvals[0].decision === 'approved' ? '批准' : '拒绝'} · {gate.approvals[0].release_reference} · {gate.approvals[0].decided_by}</p>}
      </div>
      {canReview
        ? <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs text-text-secondary">审批结论<select aria-label="Gate 审批结论" value={decision} onChange={event => setDecision(event.target.value as 'approved' | 'rejected')} className={`${ctl} mt-1 w-full`}><option value="approved">批准发布</option><option value="rejected">拒绝发布</option></select></label>
          <label className="text-xs text-text-secondary">发布引用<input aria-label="Gate 发布引用" value={releaseReference} onChange={event => setReleaseReference(event.target.value)} minLength={3} maxLength={160} pattern="[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}" className={`${ctl} mt-1 w-full`} placeholder="release/2026.11" /></label>
          <label className="text-xs text-text-secondary sm:col-span-2">审批理由<textarea aria-label="Gate 审批理由" value={rationale} onChange={event => setRationale(event.target.value)} minLength={8} maxLength={2000} rows={2} className={`${ctl} mt-1 w-full`} /></label>
          <div className="sm:col-span-2"><button type="button" className={ctlPrimary} disabled={saving || loading || !referenceValid || !rationaleValid} onClick={() => void submit()}>{saving ? '提交中…' : '提交 Gate 审批'}</button></div>
        </div>
        : <p className="text-xs text-text-muted">当前最新裁决不是 PASS，服务端不会接受发布审批。</p>}
    </>}
  </section>
}
