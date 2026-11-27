'use client'

/**
 * 运行评测：发起评测（确认单）+ 统一执行记录列表（批次 1.1 UX 重设计）。
 *
 * - 执行记录：三来源（Prompt 发布评测 / CLI 台账 / V2 方案运行）合并为单一
 *   时间倒序列表，来源徽标区分（业界"一次测评 = 一条 Run"形态）；
 *   发布评测/CLI 台账行点开逐题抽屉，V2 行保留整页详情与 Gate 审批。
 * - 发起评测 = 确认单：必填仅"被测 Prompt + 候选版本"，对比基线/题集/判分/门禁
 *   由系统按域自动推导并明示；RAG 域额外开放方式与知识库选择。
 * - 批次 1.1 保留三个一级标签（用户拍板），本组件是"运行评测"标签的内部。
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
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Empty, modeShort, MODE_LABELS, StatusBadge, TableWrap, Td, Th, timeShort } from './evalUi'
import {
  EVAL_DOMAINS,
  domainLabel,
  moduleToDomain,
  promptKeyToDomain,
  type DomainFilter,
} from '../domains'
import RunHistoryDetail from './RunHistoryDetail'
import EvalRunDetailDrawer from './EvalRunDetailDrawer'
import RunCompareDrawer from './RunCompareDrawer'
import RagasSuiteConfigPanel, { DEFAULT_RAGAS_SUITE_CONFIG, type RagasSuiteConfig } from '../RagasSuiteConfigPanel'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked'])
const RUN_PAGE_SIZE = 10
const RELEASE_TERMINAL = new Set(['failed', 'passed', 'approved', 'published', 'rolled_back'])

type RunPane = 'launch' | 'history'
/** 统一执行记录的来源徽标（来源收敛展示，不改落库结构）。 */
type HistorySource = 'release' | 'ledger' | 'v2'

const SOURCE_LABELS: Record<HistorySource, string> = {
  release: '发布评测',
  ledger: 'CLI 台账',
  v2: 'V2 运行',
}

/** SQL 发布评测的套件标记：canonical 内置集，runner 不依赖 suite 参数（仅记录可读性）。 */
const SQL_RELEASE_SUITE = 'sql_canonical'

interface UnifiedRunRow {
  key: string
  source: HistorySource
  time: string | null
  domain: string
  target: string
  targetSub: string
  suite: string
  status: React.ReactNode
  result: React.ReactNode
  gate: React.ReactNode
  actor: string
  /** release 行内门禁展开所需 */
  record?: PromptReleaseRecord
  /** v2 行操作（运行详情/Gate/取消/重试）所需 */
  v2Run?: EvaluationV2Run
}

export default function RunsTab({
  promptPrefill,
  suiteVersionPrefill,
  focusRunId,
  domain = 'all',
  onGoCompare,
}: {
  promptPrefill?: { key: string; version: string } | null
  suiteVersionPrefill?: string
  focusRunId?: string | null
  domain?: DomainFilter
  onGoCompare?: (runId: string) => void
}) {
  const queryClient = useQueryClient()
  const allowed = atLeast('super_admin')
  const [runPane, setRunPane] = useState<RunPane>('launch')
  const [historyPage, setHistoryPage] = useState(0)
  const [suiteVersionId, setSuiteVersionId] = useState('')
  const [suiteKbId, setSuiteKbId] = useState('')
  const [suiteMode, setSuiteMode] = useState('offline')
  const [relKey, setRelKey] = useState('')
  const [relVersion, setRelVersion] = useState('')
  const [relMode, setRelMode] = useState('self+ragas')
  const [relNote, setRelNote] = useState('')
  const [ragasSuite, setRagasSuite] = useState<RagasSuiteConfig>(DEFAULT_RAGAS_SUITE_CONFIG)
  const [expandedReleaseId, setExpandedReleaseId] = useState('')
  const [detailRunId, setDetailRunId] = useState('')
  // 发布评测 / CLI 台账的逐题抽屉（读报告文件口径，与 V2 整页详情互斥）
  const [detailEvalRun, setDetailEvalRun] = useState('')
  // 批次 6c：发布评测运行对比抽屉（预选记录 = 对比侧）
  const [compareReleaseId, setCompareReleaseId] = useState('')
  // 批次 6c：CLI 台账去重——prompt_publish 触发的台账与发布评测重复，默认隐藏
  const [showAllLedger, setShowAllLedger] = useState(false)
  const [gateRunId, setGateRunId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const suitesQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-suites-options'],
    enabled: allowed,
    queryFn: () => evaluationService.listSuites(),
  })
  const suites = suitesQuery.data ?? []
  const domainSuites = useMemo(() => {
    if (domain === 'all') return suites
    return suites.filter(s => moduleToDomain(s.module) === domain)
  }, [suites, domain])

  const selectedSuite = useMemo(() => {
    return suites.find(s => s.suite_version_id === suiteVersionId)
  }, [suites, suiteVersionId])

  useEffect(() => {
    if (suiteVersionPrefill) {
      setSuiteVersionId(suiteVersionPrefill)
    }
  }, [suiteVersionPrefill])

  useEffect(() => {
    if (selectedSuite) {
      if (selectedSuite.kb_id) {
        setSuiteKbId(selectedSuite.kb_id)
      } else if (moduleToDomain(selectedSuite.module) === 'rag' && !suiteKbId) {
        setSuiteKbId('rag_eval_kb')
      }
    }
  }, [selectedSuite])

  const optionsQuery = useQuery({
    queryKey: ['admin', 'evaluation-center-run-options'],
    enabled: allowed,
    queryFn: async () => {
      const promptItems = await promptsService.list().catch(() => [] as PromptListItem[])
      return { promptKeys: promptItems }
    },
  })
  const promptKeys: PromptListItem[] = optionsQuery.data?.promptKeys ?? []
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

  // 发布门禁评测记录（含 SQL 双跑等）
  const releasesQuery = useQuery({
    queryKey: ['admin', 'prompt-release-runs'],
    enabled: allowed && runPane === 'history' && !detailRunId,
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
    enabled: allowed && runPane === 'history' && !detailRunId,
    queryFn: () => evaluationService.listRuns(50),
  })
  const ledgerRuns = useMemo(() => {
    let rows = ledgerQuery.data ?? []
    // 批次 6c 台账去重：prompt_publish 触发的台账与发布评测重复（走查实证会覆盖
    // 候选展示），默认隐藏，开关兜底保留全量可查。
    if (!showAllLedger) rows = rows.filter(row => row.trigger !== 'prompt_publish')
    if (domain !== 'all') rows = rows.filter(row => moduleToDomain(row.module) === domain)
    return rows
  }, [ledgerQuery.data, domain, showAllLedger])

  // 统一执行记录：三来源合并、时间倒序混排（业界单 Run 形态，来源徽标区分）
  const unifiedRows = useMemo<UnifiedRunRow[]>(() => {
    const rows: UnifiedRunRow[] = []
    const seenRunIds = new Set<string>()

    for (const record of releases) {
      if (record.eval_run_id) seenRunIds.add(record.eval_run_id)
      rows.push({
        key: `release:${record.release_id}`,
        source: 'release',
        time: record.created_at || record.updated_at || null,
        domain: domainLabel(promptKeyToDomain(record.prompt_key)),
        target: record.prompt_key,
        targetSub: `v${record.version} · ${record.eval_suite}`,
        suite: record.eval_suite,
        status: null, // 渲染层用 record 现取（含 title 提示）
        result: (() => {
          const candidate = (record.metrics as Record<string, unknown> | undefined)?.candidate_pass_rate
          const current = (record.metrics as Record<string, unknown> | undefined)?.current_pass_rate
          if (typeof candidate !== 'number' && typeof current !== 'number') return <span className="text-text-muted">—</span>
          const pct = (v: unknown) => typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—'
          return <span className="text-xs">候选 {pct(candidate)} · 现网 {pct(current)}</span>
        })(),
        gate: record.status === 'passed' || record.status === 'approved' || record.status === 'published'
          ? <Badge text="门禁通过" tone="good" />
          : record.status === 'failed'
            ? <Badge text="失败" tone="bad" />
            : <Badge text="评测中" tone="warn" />,
        actor: record.created_by || '—',
        record,
      })
    }
    for (const run of runs) {
      if (seenRunIds.has(run.run_id)) continue
      seenRunIds.add(run.run_id)
      const firstPrompt = Object.keys(run.prompt_versions || {})[0]
      const isLedgerSource = run.source === 'ledger' || run.source === 'cli' || !run.suite_version_id
      const source: HistorySource = isLedgerSource ? 'ledger' : 'v2'
      rows.push({
        key: `${source}:${run.run_id}`,
        source,
        time: run.finished_at || run.created_at || null,
        domain: domainLabel(moduleToDomain(run.module)),
        target: firstPrompt || run.suite_name || run.suite_key || '默认配置',
        targetSub: `v${run.suite_version || '—'} · ${run.evaluation_mode ? modeShort(run.evaluation_mode) : ''}`,
        suite: run.suite_name || run.suite_key || '—',
        status: null,
        result: <span className="text-xs">{run.completed_cases}/{run.total_cases} · 通过 {run.passed_cases}</span>,
        gate: run.gate_verdict ? <Badge text={run.gate_verdict} tone={run.gate_verdict === 'PASS' ? 'good' : run.gate_verdict === 'FAIL' ? 'bad' : 'warn'} /> : <span className="text-text-muted">—</span>,
        actor: run.created_by || '—',
        v2Run: run,
      })
    }
    for (const run of ledgerRuns) {
      if (seenRunIds.has(run.run_id)) continue
      seenRunIds.add(run.run_id)
      rows.push({
        key: `ledger:${run.run_id}`,
        source: 'ledger',
        time: run.timestamp,
        domain: domainLabel(moduleToDomain(run.module)),
        target: run.module,
        targetSub: run.dataset_version || '',
        suite: run.dataset_version || '—',
        status: null, // 渲染层用 run.status 现取
        result: <span className="text-xs">{(run.pass_rate * 100).toFixed(1)}% · {run.case_count ?? '—'} 题</span>,
        gate: <span className="text-text-muted">—</span>,
        actor: run.triggered_by || run.trigger || '—',
      })
    }
    rows.sort((a, b) => {
      const at = a.time ? new Date(a.time).getTime() : 0
      const bt = b.time ? new Date(b.time).getTime() : 0
      return bt - at
    })
    return rows
  }, [releases, ledgerRuns, runs])

  // 批次 6c：同 prompt_key 可选对比记录数（≥2 才展示行内"对比"入口）
  const compareableCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const record of releases) {
      if (!record.eval_run_id) continue
      counts[record.prompt_key] = (counts[record.prompt_key] ?? 0) + 1
    }
    return counts
  }, [releases])

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
        dataset_version: { source: 'eval-center', evaluation_mode: relDomain === 'rag' ? relMode : 'offline', ...(relNote.trim() ? { note: relNote.trim() } : {}) },
      })
      setMessage(`发布评测已入队：${record.release_id.slice(0, 12)}（候选 ${relKey}@v${relVersion}）。已在下方执行记录跟踪。`)
      setRelNote('')
      await queryClient.invalidateQueries({ queryKey: ['admin', 'prompt-release-runs'] })
      setRunPane('history')
    } catch (cause) { setError(cause instanceof Error ? errMessage(cause) : '发起发布评测失败') }
    finally { setBusy(false) }
  }

  const startSuiteRun = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!suiteVersionId) throw new Error('请选择要执行的评测方案。')
      const targetDomain = selectedSuite ? moduleToDomain(selectedSuite.module) : ''
      if (targetDomain === 'rag' && !suiteKbId.trim()) {
        throw new Error('RAG 域评测方案必须指定知识库标识。')
      }
      const requestId = createIdempotencyKey('v2_run')
      const response = await evaluationService.createV2Run({
        suite_version_id: suiteVersionId,
        source: 'eval-center',
        evaluation_mode: suiteMode,
        ...(targetDomain === 'rag' || suiteKbId.trim() ? { kb_id: suiteKbId.trim() } : {}),
        request_id: requestId,
      })
      setMessage(`评测方案运行已启动：Run ID ${response.run.run_id.slice(0, 8)}。任务已入队执行，可在下方记录中跟踪。`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })
      setRunPane('history')
    } catch (cause) {
      setError(cause instanceof Error ? errMessage(cause) : '发起方案评测失败')
    } finally {
      setBusy(false)
    }
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
        执行记录
      </button>
    </div>

    {runPane === 'launch' ? <section id="evaluation-run-launch" role="tabpanel" aria-label="发起评测" className="space-y-4">
      <Card
        title="运行评测套件 (V2 综合方案)"
        subtitle="选择预设评测方案及版本，一键启动端到端全链路自动化测评（支持离线比对、语义判分与 Ragas 量化指标）。"
      >
        <div className="grid gap-3 lg:grid-cols-[1.6fr_1fr_1fr_auto]">
          <select
            aria-label="选择评测方案"
            value={suiteVersionId}
            onChange={e => setSuiteVersionId(e.target.value)}
            className={ctl}
          >
            <option value="">选择评测方案套件</option>
            {domainSuites.map(s => (
              <option key={s.suite_version_id || s.name} value={s.suite_version_id}>
                {s.name} · v{s.version} ({domainLabel(moduleToDomain(s.module))} · {s.case_count} 题)
              </option>
            ))}
          </select>

          <select
            aria-label="套件评测方式"
            value={suiteMode}
            onChange={e => setSuiteMode(e.target.value)}
            className={ctl}
          >
            {Object.entries(MODE_LABELS).map(([value, labelText]) => (
              <option key={value} value={value}>{labelText}</option>
            ))}
          </select>

          <input
            aria-label="知识库标识"
            type="text"
            placeholder="知识库标识（RAG 必填，如 rag_eval_kb）"
            value={suiteKbId}
            onChange={e => setSuiteKbId(e.target.value)}
            className={ctl}
          />

          <button
            type="button"
            className={ctlPrimary}
            disabled={busy || !suiteVersionId}
            onClick={() => void startSuiteRun()}
          >
            <Play size={13} className="mr-1 inline" aria-hidden="true" />启动方案评测
          </button>
        </div>

        {selectedSuite && (
          <div className="mt-3 space-y-1 rounded-md bg-bg-elevated px-3 py-2 text-[11px] leading-5 text-text-secondary" aria-label="套件方案概要">
            <p className="font-medium text-text-primary">{selectedSuite.name}（版本：v{selectedSuite.version}）</p>
            <p>
              业务领域：<span className="font-medium text-text-primary">{domainLabel(moduleToDomain(selectedSuite.module))}</span> ·
              用例题量：<span className="font-medium text-text-primary">{selectedSuite.case_count} 题</span> ·
              绑定数据集：<span className="font-medium text-text-primary">{selectedSuite.dataset_version}</span>
              {selectedSuite.kb_id && <> · 预设知识库：<span className="font-medium text-text-primary">{selectedSuite.kb_id}</span></>}
            </p>
            {selectedSuite.thresholds && Object.keys(selectedSuite.thresholds).length > 0 && (
              <p className="text-text-muted">
                通过门禁阈值：{Object.entries(selectedSuite.thresholds).map(([k, v]) => `${k} ≥ ${(Number(v) * 100).toFixed(0)}%`).join('，')}
              </p>
            )}
          </div>
        )}
      </Card>

      <Card title="发起评测" subtitle={'必填仅"被测 Prompt + 候选版本"；对比基线、评测题集、判分方式与门禁口径由系统按域自动推导。'}>
        <div className="grid gap-3 lg:grid-cols-[1.4fr_auto_auto_auto]">
          <select aria-label="发布评测 Prompt" value={relKey} onChange={e => setRelKey(e.target.value)} className={ctl}>
            <option value="">选择 Prompt</option>
            {domain === 'all'
              ? EVAL_DOMAINS.map(item => {
                const group = domainPromptKeys.filter(prompt => promptKeyToDomain(prompt.key) === item.key)
                if (!group.length) return null
                return <optgroup key={item.key} label={domainLabel(item.key)}>
                  {group.map(prompt => <option key={prompt.key} value={prompt.key}>{prompt.key} · {prompt.name}{prompt.latest_version_number ? `（现网 v${prompt.latest_version_number}）` : ''}</option>)}
                </optgroup>
              })
              : domainPromptKeys.map(prompt => <option key={prompt.key} value={prompt.key}>{prompt.key} · {prompt.name}{prompt.latest_version_number ? `（现网 v${prompt.latest_version_number}）` : ''}</option>)}
          </select>
          <input
            aria-label="发布评测候选版本" type="number" min={1}
            placeholder={relLatest ? `候选版本（现网 v${relLatest}）` : '候选版本'}
            title={relLatest ? `当前已发布 v${relLatest}，评测通过后候选版本将接替该版本` : undefined}
            value={relVersion} onChange={e => setRelVersion(e.target.value)} className={`${ctl} w-44`}
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
        {/* 确认单：系统自动推导的口径明示（不要求用户填写，只要求用户知情） */}
        {relKey && <div className="mt-3 space-y-0.5 rounded-md bg-bg-elevated px-3 py-2 text-[11px] leading-5 text-text-secondary" aria-label="自动推导口径">
          <p>对比基线：现网 {relLatest ? `v${relLatest}` : '尚未发布（首次评测直接落基线）'}</p>
          <p>评测题集：{suiteForRelease(relKey)}{relDomain === 'sql' ? '（canonical 15 题，含拒答与对抗）' : relDomain === 'rag' ? '（desktop_upload_smoke 8 题短集）' : '（eval_center 标记）'}——可在「数据集」页查看题目样例</p>
          <p>判分方式：{relDomain === 'sql' ? '候选 vs 现网双跑真实执行对比' : relDomain === 'rag' ? MODE_LABELS[relMode] : '离线契约断言'}</p>
          <p>门禁口径：{relDomain === 'sql' ? '样本量 + 候选回归 ≤5% + 防全挂下限' : relDomain === 'rag' ? '分层阈值（smoke ≥95%）+ 基线回归 + RAGAS（如选用）' : 'audit 留痕，不阻断发布'}</p>
        </div>}
        {relKey && relDomain === 'rag' && (
          <div className="mt-3.5">
            <RagasSuiteConfigPanel
              value={ragasSuite}
              onChange={setRagasSuite}
              disabled={busy}
            />
          </div>
        )}
        <div className="mt-3">
          <input
            aria-label="发起原因（可选）" type="text" maxLength={200}
            placeholder="发起原因（可选，进审计留痕）" value={relNote} onChange={e => setRelNote(e.target.value)} className={`${ctl} w-full`}
          />
        </div>
        <p className="mt-3 text-[11px] text-text-muted">
          SQL 类 Prompt：门禁按 canonical 15 条题集做「候选 vs 现网」双跑对比（候选不比现网差才放行）。
          RAG 类 Prompt：门禁按 desktop_upload_smoke 8 条短集跑分层阈值门（smoke ≥95%）。
          其他类别暂无专属评测模块，将回落 RAG 门禁口径并留痕。
        </p>
      </Card>
    </section> : <section id="evaluation-run-history" role="tabpanel" aria-label="执行记录" className="space-y-4">
      {detailRunId ? <RunHistoryDetail key={detailRunId} runId={detailRunId} onBack={() => setDetailRunId('')} onGoCompare={onGoCompare} /> : <Card
        title="执行记录"
        subtitle="发布评测、CLI 台账与 V2 运行合并展示，按时间倒序；来源徽标区分口径。"
        actions={<>
          <label className="mr-2 inline-flex items-center gap-1.5 text-[11px] text-text-secondary" title="发布评测触发的台账行与「发布评测」来源重复，默认隐藏">
            <input type="checkbox" checked={showAllLedger} onChange={e => setShowAllLedger(e.target.checked)} />
            显示全部台账
          </label>
          <button type="button" className={ctlGhost} disabled={releasesQuery.isFetching || ledgerQuery.isFetching || runsQuery.isFetching} onClick={() => { void releasesQuery.refetch(); void ledgerQuery.refetch(); void runsQuery.refetch() }}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />刷新</button>
        </>}
      >
        <UnifiedRunsTable
          rows={unifiedRows}
          domain={domain}
          expandedId={expandedReleaseId}
          busy={busy}
          onToggle={id => setExpandedReleaseId(current => current === id ? '' : id)}
          onOpenEvalDetail={runId => setDetailEvalRun(runId)}
          onOpenV2Detail={runId => setDetailRunId(runId)}
          onToggleGate={runId => setGateRunId(current => current === runId ? '' : runId)}
          onCancel={run => void cancel(run)}
          onRetry={run => void retry(run)}
          onGoCompare={onGoCompare}
          onOpenCompare={releaseId => setCompareReleaseId(releaseId)}
          compareableCounts={compareableCounts}
          onRetryRefresh={() => { void releasesQuery.refetch(); void ledgerQuery.refetch(); void runsQuery.refetch() }}
        />
        <EvalRunDetailDrawer runId={detailEvalRun} onClose={() => setDetailEvalRun('')} />
        {compareReleaseId && (() => {
          const focus = releases.find(item => item.release_id === compareReleaseId)
          const sameKey = focus ? releases.filter(item => item.prompt_key === focus.prompt_key && item.eval_run_id) : []
          return sameKey.length >= 2
            ? <RunCompareDrawer releases={sameKey} focusReleaseId={compareReleaseId} onClose={() => setCompareReleaseId('')} />
            : null
        })()}
      </Card>}
      {gateRunId && <GateApprovalPanel runId={gateRunId} onClose={() => setGateRunId('')} onSubmitted={() => void queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-runs'] })} />}
    </section>}
  </div>
}

// ── 统一执行记录表格（三来源合并，来源徽标区分）──────────────

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

function UnifiedRunsTable({ rows, domain, expandedId, busy, onToggle, onOpenEvalDetail, onOpenV2Detail, onToggleGate, onCancel, onRetry, onGoCompare, onOpenCompare, compareableCounts, onRetryRefresh }: {
  rows: UnifiedRunRow[]
  domain: DomainFilter
  expandedId: string
  busy: boolean
  onToggle: (id: string) => void
  onOpenEvalDetail: (runId: string) => void
  onOpenV2Detail: (runId: string) => void
  onToggleGate: (runId: string) => void
  onCancel: (run: EvaluationV2Run) => void
  onRetry: (run: EvaluationV2Run) => void
  onGoCompare?: (runId: string) => void
  onOpenCompare: (releaseId: string) => void
  compareableCounts: Record<string, number>
  onRetryRefresh: () => void
}) {
  if (rows.length === 0) return <div className="space-y-2 py-5 text-center">
    <Empty text={domain === 'all' ? '还没有测评记录。切到"发起评测"开始第一次。' : '该域暂无测评记录。切到"发起评测"发起第一次。'} />
    <button type="button" className={ctl} onClick={onRetryRefresh}>刷新</button>
  </div>
  return <TableWrap minWidth={1240}>
    <thead className="bg-bg-elevated"><tr><Th>时间</Th><Th>域</Th><Th>来源</Th><Th>被测对象</Th><Th>题集</Th><Th>结果</Th><Th>状态</Th><Th>门禁</Th><Th>发起方</Th><Th end>操作</Th></tr></thead>
    <tbody className="divide-y divide-border-subtle">
      {rows.map(row => {
        const record = row.record
        const blocked = record ? ((record.metrics as { gate?: { blocked_rules?: Array<{ rule?: string; message?: string }> } } | undefined)?.gate?.blocked_rules ?? []) : []
        const statusHint = record?.failure_reason
          || (record?.eval_run_id?.startsWith('local-') ? 'CLI 本地执行失败（非门禁拦截）' : '')
        const runId = row.source === 'v2' ? row.key.slice('v2:'.length)
          : row.source === 'ledger' ? row.key.slice('ledger:'.length)
          : row.record?.release_id || row.key
        return (<Fragment key={row.key}><tr>
          <Td>{timeShort(row.time)}</Td>
          <Td><Badge text={row.domain} tone="blue" /></Td>
          <Td>
            <Badge text={SOURCE_LABELS[row.source]} tone={row.source === 'release' ? 'accent' : row.source === 'ledger' ? 'gray' : 'blue'} />
            {row.source === 'v2' && row.v2Run?.source && row.v2Run.source !== 'manual' && <span className="ml-1"><Badge text={row.v2Run.source === 'online' ? '线上采样' : row.v2Run.source === 'prompt_release' ? '发布' : row.v2Run.source} tone="accent" /></span>}
          </Td>
          <Td><span className="block text-text-primary">{row.target}</span><span className="text-[11px] text-text-muted">{row.targetSub}</span></Td>
          <Td className="max-w-40 break-words">{row.suite}</Td>
          <Td>{row.result}</Td>
          <Td>
            {row.source === 'release' && record
              ? <span title={statusHint || undefined}><StatusBadge status={record.status} /></span>
              : row.source === 'ledger' && row.status === null
                ? null
                : row.status}
          </Td>
          <Td>{row.gate}</Td>
          <Td>{row.actor}</Td>
          <Td end>
            {row.source === 'release' && record && <>
              <button type="button" className={ctlGhost} onClick={() => onToggle(record.release_id)}>{expandedId === record.release_id ? '收起' : '门禁详情'}</button>
              {record.eval_run_id && <button type="button" className={ctlGhost} onClick={() => onOpenEvalDetail(record.eval_run_id!)}>逐题</button>}
              {record.eval_run_id && (compareableCounts[record.prompt_key] ?? 0) >= 2 && <button type="button" className={ctlGhost} onClick={() => onOpenCompare(record.release_id)}>对比</button>}
            </>}
            {row.source === 'ledger' && <button type="button" className={ctlGhost} onClick={() => onOpenEvalDetail(runId)}>逐题</button>}
            {row.source === 'v2' && row.v2Run && <>
              <button type="button" className={ctlGhost} onClick={() => onOpenV2Detail(runId)}>运行详情</button>
              {row.v2Run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => onToggleGate(runId)}>{row.v2Run.gate_verdict === 'PASS' ? 'Gate 审批' : 'Gate 详情'}</button>}
              {onGoCompare && row.v2Run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => onGoCompare(runId)}>对比<ArrowRight size={11} className="ml-0.5 inline" aria-hidden="true" /></button>}
              {!TERMINAL.has(row.v2Run.job_status) && <button type="button" className={`${ctlGhost} mr-1`} disabled={busy} onClick={() => onCancel(row.v2Run!)}><X size={12} className="mr-0.5 inline" aria-hidden="true" />取消</button>}
              {row.v2Run.job_status === 'failed' && <button type="button" className={ctl} disabled={busy} onClick={() => onRetry(row.v2Run!)}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />重试</button>}
            </>}
          </Td>
        </tr>
        {row.source === 'release' && record && expandedId === record.release_id && <tr><td colSpan={10} className="bg-bg-elevated/50 p-3">
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

// ── Gate 审批面板（V2 运行通过后的发布意见）──────────────

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
