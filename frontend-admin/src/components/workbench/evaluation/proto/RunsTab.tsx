'use client'

/**
 * 运行评测：发起运行与分页历史分开；历史记录进入运行详情页后逐题查看脱敏结果。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, Play, RotateCw, X } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  evaluationService,
  type EvaluationV2Gate,
  type EvaluationSuite,
  type EvaluationV2Run,
} from '@/api/evaluation'
import { createIdempotencyKey, errMessage } from '@/api/client'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { promptsService, type PromptListItem, type PromptReleaseRecord } from '@/api/prompts'
import { atLeast } from '@/lib/auth'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Empty, modeShort, MODE_LABELS, StatusBadge, TableWrap, Td, Th, timeShort, fmtCost } from './evalUi'
import RunHistoryDetail from './RunHistoryDetail'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked'])
const RUN_PAGE_SIZE = 10
const RELEASE_TERMINAL = new Set(['failed', 'passed', 'approved', 'published', 'rolled_back'])

type RunPane = 'launch' | 'history'
// 2026-11-22：评测中心是评测的统一入口——运行记录聚合三类来源
// （V2 方案 / Prompt 发布评测 / CLI 台账），发起评测支持两种类型。
type HistorySource = 'v2' | 'release' | 'ledger'
type LaunchKind = 'v2' | 'release'

export default function RunsTab({ promptPrefill, focusRunId, focusSuiteVersionId, onGoCompare }: { promptPrefill?: { key: string; version: string } | null; focusRunId?: string | null; focusSuiteVersionId?: string | null; onGoCompare?: (runId: string) => void }) {
  const queryClient = useQueryClient()
  const allowed = atLeast('super_admin')
  const [runPane, setRunPane] = useState<RunPane>('launch')
  const [historyPage, setHistoryPage] = useState(0)
  const [historySource, setHistorySource] = useState<HistorySource>('v2')
  const [launchKind, setLaunchKind] = useState<LaunchKind>('v2')
  const [suiteId, setSuiteId] = useState('')
  const [mode, setMode] = useState('self+ragas')
  const [promptKey, setPromptKey] = useState('')
  const [promptVersion, setPromptVersion] = useState('')
  const [kbId, setKbId] = useState('')
  const [relKey, setRelKey] = useState('')
  const [relVersion, setRelVersion] = useState('')
  const [relMode, setRelMode] = useState('self+ragas')
  const [expandedReleaseId, setExpandedReleaseId] = useState('')
  const [detailRunId, setDetailRunId] = useState('')
  const [gateRunId, setGateRunId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const optionsQuery = useQuery({
    queryKey: ['admin', 'evaluation-center-run-options'],
    enabled: allowed,
    queryFn: async () => {
      const [suiteItems, kbCatalog, promptItems] = await Promise.all([
        evaluationService.listSuites(),
        listKbAuthority().catch(() => ({ knowledge_bases: [] as KbAuthorityItem[] })),
        promptsService.list().catch(() => [] as PromptListItem[]),
      ])
      return {
        suites: suiteItems.filter(item => item.source === 'evaluation_v2' && item.status === 'published' && item.suite_version_id),
        kbs: kbCatalog.knowledge_bases.filter(item => item.active && !item.alias_for && !item.deprecated),
        promptKeys: promptItems,
      }
    },
  })
  const suites: EvaluationSuite[] = optionsQuery.data?.suites ?? []
  const kbs: KbAuthorityItem[] = optionsQuery.data?.kbs ?? []
  const promptKeys: PromptListItem[] = optionsQuery.data?.promptKeys ?? []
  const runsQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-runs', historyPage],
    enabled: allowed && runPane === 'history' && !detailRunId,
    queryFn: async () => {
      const rows = await evaluationService.listV2Runs(RUN_PAGE_SIZE + 1, historyPage * RUN_PAGE_SIZE)
      return { rows: rows.slice(0, RUN_PAGE_SIZE), hasNext: rows.length > RUN_PAGE_SIZE }
    },
    refetchInterval: query => query.state.data?.rows.some(run => !TERMINAL.has(run.job_status)) ? 3000 : false,
  })
  const runs = runsQuery.data?.rows ?? []

  // 发布门禁评测记录（含 SQL 双跑等，2026-11-22 聚合进评测中心）
  const releasesQuery = useQuery({
    queryKey: ['admin', 'prompt-release-runs'],
    enabled: allowed && runPane === 'history' && historySource === 'release' && !detailRunId,
    queryFn: () => promptsService.listAllReleases(50),
    refetchInterval: query => query.state.data?.some(item => !RELEASE_TERMINAL.has(item.status)) ? 4000 : false,
  })
  const releases = releasesQuery.data ?? []

  // CLI 台账（ai.eval_run_records：CLI / 线上采样 / 历史口径评测）
  const ledgerQuery = useQuery({
    queryKey: ['admin', 'evaluation-ledger-runs'],
    enabled: allowed && runPane === 'history' && historySource === 'ledger' && !detailRunId,
    queryFn: () => evaluationService.listRuns(50),
  })
  const ledgerRuns = ledgerQuery.data ?? []

  useEffect(() => {
    if (focusSuiteVersionId && suites.some(item => item.suite_version_id === focusSuiteVersionId)) {
      setSuiteId(focusSuiteVersionId)
    }
  }, [focusSuiteVersionId, suites])
  useEffect(() => {
    const firstSuiteId = suites[0]?.suite_version_id
    if (!firstSuiteId) return
    setSuiteId(current => suites.some(item => item.suite_version_id === current) ? current : firstSuiteId)
  }, [suites])
  useEffect(() => {
    if (!kbs.length) return
    setKbId(current => kbs.some(item => item.kb_id === current) ? current : kbs[0].kb_id)
  }, [kbs])
  useEffect(() => {
    if (!promptPrefill) return
    const version = promptPrefill.version.trim()
    if (!/^\d+$/.test(version)) return
    setPromptKey(promptPrefill.key)
    setPromptVersion(version)
  }, [promptPrefill])
  useEffect(() => {
    if (!focusRunId) return
    setDetailRunId(focusRunId)
    setRunPane('history')
  }, [focusRunId])

  const selectedSuite = useMemo(() => suites.find(item => item.suite_version_id === suiteId), [suites, suiteId])
  const isRag = selectedSuite?.module === 'rag'

  const start = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!selectedSuite) throw new Error('请选择已发布的数据集方案。')
      if (isRag && !kbId) throw new Error('请选择有权访问的知识库。')
      const promptVersions: Record<string, number> = {}
      if (promptKey.trim() && /^\d+$/.test(promptVersion.trim())) promptVersions[promptKey.trim()] = Number(promptVersion.trim())
      const result = await evaluationService.createV2Run({
        suite_version_id: suiteId,
        source: 'manual',
        evaluation_mode: isRag ? mode : 'offline',
        ...(isRag ? { kb_id: kbId } : {}),
        request_id: createIdempotencyKey(),
        ...(Object.keys(promptVersions).length ? { prompt_versions: promptVersions } : {}),
      })
      setMessage(`运行已入队：${result.run.run_id.slice(0, 8)}，配置与语料快照由服务端冻结。`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '创建运行失败') }
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

  // 2026-11-22：从评测中心发起 Prompt 发布评测（复用发布门禁链路：候选
  // 版本经评测与审批后才能发布；SQL 类 prompt 在该链路上跑真实生成评测）。
  const startRelease = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!relKey.trim()) throw new Error('请选择要评测的 Prompt。')
      if (!/^\d+$/.test(relVersion.trim())) throw new Error('请填写要评测的候选版本号（正整数）。')
      const record = await promptsService.createRelease(relKey.trim(), Number(relVersion.trim()), {
        suite: 'eval_center',
        evaluation_mode: relMode as 'offline' | 'semantic' | 'ragas' | 'self+ragas',
        executor: 'local',
        dataset_version: { source: 'eval-center', evaluation_mode: relMode },
      })
      setMessage(`发布评测已入队：${record.release_id.slice(0, 12)}（候选 ${relKey}@v${relVersion}）。切到“运行记录 → 发布评测”查看进度。`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'prompt-release-runs'] })
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '发起发布评测失败') }
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
      <Card title="发起评测" subtitle="运行固定数据集版本与模型 / Prompt / 知识库快照；两次运行在相同口径下才可直接对比。">
        <div className="mb-4 flex gap-1 border-b border-border-subtle">
          <button type="button" role="tab" aria-selected={launchKind === 'v2'} onClick={() => setLaunchKind('v2')} className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium ${launchKind === 'v2' ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
            数据集方案评测
          </button>
          <button type="button" role="tab" aria-selected={launchKind === 'release'} onClick={() => setLaunchKind('release')} className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium ${launchKind === 'release' ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
            Prompt 发布评测
          </button>
        </div>
        {launchKind === 'release' ? <>
          <p className="mb-3 text-xs text-text-secondary">
            对候选 Prompt 版本执行发布门禁评测（复用发布链路；SQL 类 Prompt 在此链路做真实生成评测）。
            评测通过后到「Prompt CI/CD」对应 Prompt 页完成审批与发布。
          </p>
          <div className="grid gap-3 lg:grid-cols-[1.4fr_auto_auto_auto]">
            <select aria-label="发布评测 Prompt" value={relKey} onChange={e => setRelKey(e.target.value)} className={ctl}>
              <option value="">选择 Prompt</option>
              {promptKeys.map(item => <option key={item.key} value={item.key}>{item.key} · {item.name}</option>)}
            </select>
            <input aria-label="发布评测候选版本" type="number" min={1} placeholder="候选版本" value={relVersion} onChange={e => setRelVersion(e.target.value)} className={`${ctl} w-28`} />
            <select aria-label="发布评测方式" value={relMode} onChange={e => setRelMode(e.target.value)} className={ctl}>
              {Object.entries(MODE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <button type="button" className={ctlPrimary} disabled={busy || !relKey} onClick={() => void startRelease()}><Play size={13} className="mr-1 inline" aria-hidden="true" />开始评测</button>
          </div>
          <p className="mt-3 text-[11px] text-text-muted">
            SQL 类 Prompt：门禁按 canonical 15 条题集做「候选 vs 现网」双跑对比（候选不比现网差才放行）。
          </p>
        </> : optionsQuery.isPending ? <Empty text="正在读取可用数据集和运行配置…" /> : <>
          <div className="grid gap-3 lg:grid-cols-[2fr_1.4fr_auto]">
            <select aria-label="数据集" value={suiteId} onChange={e => setSuiteId(e.target.value)} className={ctl}>
              <option value="">选择已发布数据集方案</option>
              {suites.map(item => <option key={item.suite_version_id} value={item.suite_version_id}>{item.name} · {item.module} · v{item.version}（{item.case_count} 条）</option>)}
            </select>
            {isRag ? <select aria-label="评测方式" value={mode} onChange={e => setMode(e.target.value)} className={ctl}>
              {Object.entries(MODE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select> : <p className="self-center text-xs text-text-muted" title="非 RAG 模块的 V2 运行按设计只做离线契约校验（不调模型、不出站）；SQL 在线生成评测请切到“Prompt 发布评测”">离线契约评测（非 RAG 暂不支持在线生成，见提示）</p>}
            <button type="button" className={ctlPrimary} disabled={busy || !suiteId || (isRag && !kbId)} onClick={() => void start()}><Play size={13} className="mr-1 inline" aria-hidden="true" />开始运行</button>
          </div>
          <div className="mt-3 grid gap-3 lg:grid-cols-[1.2fr_1fr_1.2fr]">
            <div className="flex items-center gap-2">
              <select aria-label="选择 Prompt" value={promptKey} onChange={e => setPromptKey(e.target.value)} className={`${ctl} flex-1`}>
                <option value="">被测 Prompt（可选）</option>
                {promptKeys.map(item => <option key={item.key} value={item.key}>{item.key} · {item.name}</option>)}
              </select>
              <input aria-label="Prompt 版本号" type="number" min={1} placeholder="版本" value={promptVersion} onChange={e => setPromptVersion(e.target.value)} className={`${ctl} w-20`} />
            </div>
            {isRag
              ? <select aria-label="知识库快照" value={kbId} onChange={e => setKbId(e.target.value)} className={ctl}>
                <option value="">选择知识库（运行时自动冻结）</option>
                {kbs.map(item => <option key={item.kb_id} value={item.kb_id}>{item.name} · {item.kb_id}</option>)}
              </select>
              : <p className="self-center text-xs text-text-muted">非 RAG 方案：通过既有离线 Runner 执行，不调用模型与知识库。</p>}
            <p className="self-center text-xs text-text-muted">{!isRag || mode === 'offline' ? '离线模式不调用模型，结果可复现、零成本。' : mode === 'ragas' ? 'RAGAS 每题多次 Judge 调用，慢且消耗预算。' : '在线模式将真实调用模型生成回答。'}</p>
          </div>
        </>}
      </Card>
    </section> : <section id="evaluation-run-history" role="tabpanel" aria-label="运行记录" className="space-y-4">
      {detailRunId ? <RunHistoryDetail key={detailRunId} runId={detailRunId} onBack={() => setDetailRunId('')} onGoCompare={onGoCompare} /> : <Card
        title="运行记录"
        subtitle="按创建时间倒序；覆盖 V2 方案评测、Prompt 发布评测与 CLI 台账三类来源。"
        actions={<button type="button" className={ctlGhost} disabled={(historySource === 'v2' && runsQuery.isFetching) || (historySource === 'release' && releasesQuery.isFetching) || (historySource === 'ledger' && ledgerQuery.isFetching)} onClick={() => { if (historySource === 'v2') void runsQuery.refetch(); else if (historySource === 'release') void releasesQuery.refetch(); else void ledgerQuery.refetch() }}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />刷新</button>}
      >
        <div className="mb-4 flex gap-1 border-b border-border-subtle">
          {([['v2', 'V2 方案评测'], ['release', '发布评测'], ['ledger', 'CLI 台账']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={historySource === value} onClick={() => setHistorySource(value)} className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium ${historySource === value ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text-primary'}`}>
            {label}
          </button>)}
        </div>
        {historySource === 'release' ? <ReleaseRunsTable
          loading={releasesQuery.isPending}
          error={releasesQuery.isError ? errMessage(releasesQuery.error) : ''}
          releases={releases}
          expandedId={expandedReleaseId}
          onToggle={id => setExpandedReleaseId(current => current === id ? '' : id)}
          onRetryRefresh={() => void releasesQuery.refetch()}
        /> : historySource === 'ledger' ? <LedgerRunsTable
          loading={ledgerQuery.isPending}
          error={ledgerQuery.isError ? errMessage(ledgerQuery.error) : ''}
          runs={ledgerRuns}
        /> : <>
        {runsQuery.isPending ? <Empty text="正在读取运行记录…" /> : runsQuery.isError ? <div className="space-y-2 py-5 text-center">
          <p role="alert" className="text-xs text-red-700">{errMessage(runsQuery.error)}</p>
          <button type="button" className={ctl} onClick={() => void runsQuery.refetch()}>重新读取</button>
        </div> : runs.length === 0 ? <Empty text={historyPage === 0 ? '还没有运行记录。切到“发起评测”开始第一次运行。' : '这一页没有运行记录。'} /> : <>
          <TableWrap minWidth={1120}>
            <thead className="bg-bg-elevated"><tr><Th>Run</Th><Th>数据集</Th><Th>被测配置</Th><Th>评测方式</Th><Th>状态</Th><Th>Gate</Th><Th>进度 / 结果</Th><Th end>费用</Th><Th end>耗时</Th><Th>完成时间</Th><Th end>操作</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {runs.map(run => <Fragment key={run.run_id}><tr>
                <Td mono>{run.run_id.slice(0, 8)}</Td>
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
                  {run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => onGoCompare?.(run.run_id)}>对比<ArrowRight size={11} className="ml-0.5 inline" aria-hidden="true" /></button>}
                  {!TERMINAL.has(run.job_status) && <button type="button" className={`${ctlGhost} mr-1`} disabled={busy} onClick={() => void cancel(run)}><X size={12} className="mr-0.5 inline" aria-hidden="true" />取消</button>}
                  {run.job_status === 'failed' && <button type="button" className={ctl} disabled={busy} onClick={() => void retry(run)}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />重试</button>}
                </Td>
              </tr>
              {gateRunId === run.run_id && <tr><td colSpan={11} className="bg-bg-elevated/50 p-3"><GateApprovalPanel runId={run.run_id} onClose={() => setGateRunId('')} onSubmitted={() => void queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })} /></td></tr>}
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

function ReleaseRunsTable({ loading, error, releases, expandedId, onToggle, onRetryRefresh }: {
  loading: boolean
  error: string
  releases: PromptReleaseRecord[]
  expandedId: string
  onToggle: (id: string) => void
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
  if (releases.length === 0) return <Empty text="还没有发布评测记录。切到“发起评测 → Prompt 发布评测”发起第一次。" />
  return <TableWrap minWidth={1040}>
    <thead className="bg-bg-elevated"><tr><Th>Release</Th><Th>候选 Prompt</Th><Th>方式</Th><Th>状态</Th><Th>候选 vs 现网</Th><Th>门禁</Th><Th>发起人</Th><Th>时间</Th><Th end>操作</Th></tr></thead>
    <tbody className="divide-y divide-border-subtle">
      {releases.map(record => {
        const blocked = releaseGate(record)
        return (<Fragment key={record.release_id}><tr>
          <Td mono>{record.release_id.slice(0, 12)}</Td>
          <Td><span className="block text-text-primary">{record.prompt_key}</span><span className="text-[11px] text-text-muted">v{record.version} · {record.eval_suite}</span></Td>
          <Td>{record.executor === 'local' ? '本机执行' : 'GitHub 执行'}</Td>
          <Td><StatusBadge status={record.status} /></Td>
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
          </Td>
        </tr>
        {expandedId === record.release_id && <tr><td colSpan={9} className="bg-bg-elevated/50 p-3">
          <div className="grid gap-3 text-[11px] lg:grid-cols-2">
            <div>
              <p className="mb-1 font-medium text-text-secondary">门禁判定（metrics.gate）</p>
              {(() => {
                const gate = (record.metrics as { gate?: Record<string, unknown> } | undefined)?.gate
                if (!gate) return <p className="text-text-muted">评测未产出门禁数据（pending/running 或旧记录）。</p>
                return <ul className="space-y-1 text-text-secondary">
                  {Object.entries(gate).filter(([key]) => !['blocked_rules', 'tiers', 'regression', 'ragas', 'sample_gate'].includes(key)).map(([key, value]) => (
                    <li key={key}><span className="text-text-muted">{key}:</span> {typeof value === 'boolean' ? (value ? '通过' : '未通过') : JSON.stringify(value)}</li>
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

function LedgerRunsTable({ loading, error, runs }: {
  loading: boolean
  error: string
  runs: Awaited<ReturnType<typeof evaluationService.listRuns>>
}) {
  if (loading) return <Empty text="正在读取 CLI 台账…" />
  if (error) return <div className="space-y-2 py-5 text-center">
    <p role="alert" className="text-xs text-red-700">{error}</p>
  </div>
  if (runs.length === 0) return <Empty text="台账暂无记录（CLI 评测与线上采样写入此台账）。" />
  return <TableWrap minWidth={900}>
    <thead className="bg-bg-elevated"><tr><Th>Run</Th><Th>模块</Th><Th>状态</Th><Th>通过率</Th><Th>样本</Th><Th>数据集版本</Th><Th>触发</Th><Th>时间</Th></tr></thead>
    <tbody className="divide-y divide-border-subtle">
      {runs.map(run => <tr key={run.run_id}>
        <Td mono>{run.run_id.slice(0, 16)}</Td>
        <Td>{run.module}</Td>
        <Td>{run.status ? <StatusBadge status={run.status} /> : <span className="text-text-muted">—</span>}</Td>
        <Td>{(run.pass_rate * 100).toFixed(1)}%</Td>
        <Td>{run.case_count ?? '—'}</Td>
        <Td>{run.dataset_version || '—'}</Td>
        <Td>{run.trigger || run.triggered_by || '—'}</Td>
        <Td>{timeShort(run.timestamp)}</Td>
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
    const pending = pendingRequest.current?.key === key
      ? pendingRequest.current
      : { key, requestId: createIdempotencyKey() }
    pendingRequest.current = pending
    setSaving(true); setError(''); setMessage('')
    try {
      const approval = await evaluationService.createV2GateApproval(runId, {
        gate_decision_id: latest.decision_id,
        request_id: pending.requestId,
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
