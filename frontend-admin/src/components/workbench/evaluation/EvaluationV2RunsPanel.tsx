'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { evaluationService, type EvaluationSuite, type EvaluationV2Run, type EvaluationV2RunResult } from '@/api/evaluation'
import { createIdempotencyKey } from '@/api/client'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { atLeast } from '@/lib/auth'
import EvaluationV2RunCaseDetailButton from './EvaluationV2RunCaseDetailButton'
import EvaluationHumanReviewButton from './EvaluationHumanReviewButton'
import { formatEvaluationRunCost } from './evaluationRunFormatting'
import { EvalBadge, EvalButton, EvalCard, EvalEmpty, EvalInfoBox, EvalPageHeader, evalControl } from './evalUi'

const terminal = new Set(['completed', 'failed', 'cancelled', 'blocked'])
const statusLabels: Record<string, string> = {
  queued: '排队中', preparing: '准备快照', running: '运行中', aggregating: '汇总结果',
  completed: '已完成', failed: '执行失败', cancelled: '已取消', blocked: '已阻断',
}
const scoreLabels: Record<string, string> = {
  answer_generation_prompt_tokens: '生成输入 Token',
  answer_generation_completion_tokens: '生成输出 Token',
  answer_generation_total_tokens: '生成合计 Token',
}

function gateBadge(run: EvaluationV2Run) {
  if (!run.gate_verdict) return <span className="text-xs text-text-muted">尚无裁决</span>
  if (run.gate_verdict === 'PASS') return <EvalBadge text="PASS · 通过" tone="good" />
  if (run.gate_verdict === 'FAIL') return <EvalBadge text="FAIL · 未通过" tone="bad" />
  return <EvalBadge text="INVALID · 无效" tone="warn" />
}

function runStatusBadge(run: EvaluationV2Run) {
  const status = run.job_status
  if (status === 'completed') return <EvalBadge text={statusLabels[status]} tone="good" />
  if (['failed', 'blocked'].includes(status)) return <EvalBadge text={statusLabels[status]} tone="bad" />
  if (status === 'cancelled') return <EvalBadge text={statusLabels[status]} tone="warn" />
  return <EvalBadge text={statusLabels[status] || status} tone="blue" />
}

export default function EvaluationV2RunsPanel() {
  const [runs, setRuns] = useState<EvaluationV2Run[]>([])
  const [suites, setSuites] = useState<EvaluationSuite[]>([])
  const [knowledgeBases, setKnowledgeBases] = useState<KbAuthorityItem[]>([])
  const [suiteVersionId, setSuiteVersionId] = useState('')
  const [kbId, setKbId] = useState('')
  const [subjectUserId, setSubjectUserId] = useState('')
  const [promptVersionsText, setPromptVersionsText] = useState('{}')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [results, setResults] = useState<EvaluationV2RunResult[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    if (!atLeast('super_admin')) { setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const [runItems, suiteItems, kbCatalog] = await Promise.all([
        evaluationService.listV2Runs(100),
        evaluationService.listSuites(),
        listKbAuthority(),
      ])
      setRuns(runItems)
      setSuites(suiteItems.filter(item => item.source === 'evaluation_v2' && item.status === 'published' && item.suite_version_id))
      const kbs = kbCatalog.knowledge_bases.filter(item => item.active && !item.alias_for && !item.deprecated)
      setKnowledgeBases(kbs)
      setSuiteVersionId(current => suiteItems.some(item => item.suite_version_id === current && item.status === 'published')
        ? current
        : suiteItems.find(item => item.source === 'evaluation_v2' && item.status === 'published' && item.suite_version_id)?.suite_version_id || '')
      setKbId(current => kbs.some(item => item.kb_id === current) ? current : kbs[0]?.kb_id || '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取 V2 运行资产失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!runs.length || !runs.some(run => !terminal.has(run.job_status))) return
    const timer = setInterval(() => {
      void evaluationService.listV2Runs(100).then(setRuns).catch(cause => setError(cause instanceof Error ? cause.message : '刷新运行状态失败'))
    }, 2500)
    return () => clearInterval(timer)
  }, [runs])

  useEffect(() => {
    if (!selectedRunId) { setResults([]); return }
    let active = true
    void evaluationService.listV2RunResults(selectedRunId).then(value => { if (active) setResults(value) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : '读取逐例结果失败') })
    return () => { active = false }
  }, [selectedRunId, runs.find(run => run.run_id === selectedRunId)?.job_status])

  const publishedSuites = useMemo(() => suites, [suites])
  const selectedSuite = publishedSuites.find(item => item.suite_version_id === suiteVersionId)
  const isRagSuite = selectedSuite?.module === 'rag'
  const isContractOnlySuite = ['sql', 'cs', 'e2e'].includes(selectedSuite?.module || '')

  const start = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!selectedSuite) throw new Error('请选择已发布的评测方案。')
      if (isRagSuite && !kbId) throw new Error('请选择有权访问的知识库。')
      let promptVersions: Record<string, number | string | null> | undefined
      if (isRagSuite) {
        const parsed = JSON.parse(promptVersionsText) as unknown
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Prompt 版本必须是 JSON 对象。')
        promptVersions = parsed as Record<string, number | string | null>
      }
      const result = await evaluationService.createV2Run({
        suite_version_id: suiteVersionId,
        ...(isRagSuite ? { kb_id: kbId } : {}),
        request_id: createIdempotencyKey(),
        ...(isRagSuite && subjectUserId.trim() ? { subject_user_id: Number(subjectUserId) } : {}),
        ...(promptVersions ? { prompt_versions: promptVersions } : {}),
      })
      setSelectedRunId(result.run.run_id)
      setMessage(`运行已入队：${result.run.run_id}。固定语料、任务状态与逐例结果由服务端记录。`)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建评测运行失败')
    } finally { setBusy(false) }
  }

  const cancel = async (run: EvaluationV2Run) => {
    setBusy(true); setError('')
    try {
      const response = await evaluationService.cancelV2Run(run.run_id)
      setMessage(response.accepted ? `已发送运行取消请求：${run.run_id}` : `运行已进入终态：${statusLabels[response.job_status] || response.job_status}`)
      await load()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '取消运行失败') }
    finally { setBusy(false) }
  }

  const retry = async (run: EvaluationV2Run) => {
    setBusy(true); setError('')
    try {
      await evaluationService.retryV2Run(run.run_id)
      setMessage(`已请求从固定 Run 快照恢复：${run.run_id}`)
      await load()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '恢复运行失败') }
    finally { setBusy(false) }
  }

  const selectedRun = runs.find(run => run.run_id === selectedRunId)

  if (!atLeast('super_admin')) return <p className="p-5 text-sm text-text-secondary">V2 运行沿用现有任务授权，只允许平台超级管理员启动和查看逐例结果。</p>
  if (loading && !runs.length) return <p className="p-5 text-sm text-text-secondary" role="status">正在读取 V2 运行状态…</p>

  return <div>
    <EvalPageHeader
      title="实验运行"
      subtitle="在已发布方案、固定语料快照和评分器版本上运行真实 Runner，保留可复现配置；运行环境和分数显式区分有效与无效。"
    />
    <div className="mb-4 grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(280px,1fr)]">
      <EvalCard
        title="创建评测 Run"
        subtitle="服务端固定已发布 Golden、评分规则和适用的语料快照，再由现有可恢复任务 Runner 执行。"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-text-secondary">已发布方案（Suite 版本）<select aria-label="已发布方案" value={suiteVersionId} onChange={event => setSuiteVersionId(event.target.value)} className={`${evalControl} mt-1 w-full`}><option value="">选择方案版本</option>{publishedSuites.map(item => <option key={item.suite_version_id} value={item.suite_version_id}>{item.name} · {item.module} · v{item.version}</option>)}</select></label>
          {isRagSuite && <label className="text-xs text-text-secondary">目标知识库<select aria-label="目标知识库" value={kbId} onChange={event => setKbId(event.target.value)} className={`${evalControl} mt-1 w-full`}><option value="">选择有权访问的知识库</option>{knowledgeBases.map(item => <option key={item.kb_id} value={item.kb_id}>{item.name} · {item.kb_id}</option>)}</select></label>}
          {isRagSuite && <label className="text-xs text-text-secondary">权限拒答测试用户（仅权限拒答题需要）<input aria-label="权限拒答测试用户" type="number" min={1} value={subjectUserId} onChange={event => setSubjectUserId(event.target.value)} placeholder="可留空" className={`${evalControl} mt-1 w-full`} /></label>}
          {isRagSuite && <label className="text-xs text-text-secondary sm:col-span-2">候选 Prompt 版本 JSON（用于 A/B 对比）<textarea aria-label="候选 Prompt 版本 JSON" value={promptVersionsText} onChange={event => setPromptVersionsText(event.target.value)} rows={2} className={`${evalControl} mt-1 w-full font-mono text-xs`} /></label>}
        </div>
        <div className="mt-4"><EvalButton variant="primary" onClick={() => void start()} disabled={busy || !suiteVersionId || (isRagSuite && !kbId)}>▶ 创建并运行实验</EvalButton></div>
        {selectedSuite && <div className="mt-3"><EvalInfoBox>{isContractOnlySuite ? '此模块当前仅校验 Golden 契约；运行会生成逐例记录，但 Gate 固定为 INVALID。' : isRagSuite ? 'RAG 运行会固定经过授权的知识库语料快照；旧 Runner 未提供费用账单归因时仍标记为未报告。' : '此模块通过已有离线 Runner 执行，不会调用模型或真实 Provider。'}</EvalInfoBox></div>}
        {publishedSuites.length === 0 && <div className="mt-3"><EvalInfoBox>还没有已发布方案。先在“评测方案”中发布绑定 Golden 与机器评分器的版本。</EvalInfoBox></div>}
      </EvalCard>

      <EvalCard title="最近实验摘要" subtitle="任务状态与服务端 Gate 裁决独立展示。">
        {runs.slice(0, 3).map(run => (
          <button key={run.run_id} type="button" onClick={() => setSelectedRunId(run.run_id)} className="flex w-full items-start gap-3 border-b border-border-subtle py-3 text-left last:border-b-0 hover:bg-bg-hover">
            <span className={'grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold ' + (run.gate_verdict === 'PASS' ? 'bg-emerald-50 text-emerald-700' : run.gate_verdict === 'FAIL' ? 'bg-red-50 text-red-700' : 'bg-bg-elevated text-text-secondary')}>
              {run.gate_verdict === 'FAIL' ? '!' : '✓'}
            </span>
            <span className="min-w-0 flex-1">
              <strong className="block truncate text-sm text-text-primary">{run.suite_name || run.suite_key || '评测运行'} · v{run.suite_version || '—'}</strong>
              <span className="block truncate text-xs text-text-muted">{run.run_id.slice(0, 8)} · {run.completed_cases}/{run.total_cases} 例 · {formatEvaluationRunCost(run.cost_amount, run.cost_currency)}</span>
            </span>
            <span className="shrink-0">{gateBadge(run)}</span>
          </button>
        ))}
        {runs.length === 0 && <EvalEmpty>暂无运行记录。创建实验后在此查看摘要。</EvalEmpty>}
        <div className="mt-3"><EvalInfoBox>费用未归因时显示“未归因”，不使用展示逻辑伪造费用。</EvalInfoBox></div>
      </EvalCard>
    </div>

    {error && <p className="mb-3 rounded-md border border-red-200 p-3 text-sm text-red-800" role="alert">{error}</p>}
    {message && <p className="mb-3 text-sm text-text-secondary" role="status">{message}</p>}

    <EvalCard
      title="实验记录"
      subtitle="运行结果与冻结的配置指纹；无效运行不会伪装成质量失败。"
      actions={<EvalButton size="sm" onClick={() => void load()} disabled={busy || loading}>刷新</EvalButton>}
      bodyClassName="overflow-x-auto p-0"
    >
      <table className="w-full min-w-[950px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">Suite</th><th className="px-4 py-3">Run</th><th className="px-4 py-3">任务状态</th><th className="px-4 py-3">Gate</th><th className="px-4 py-3">样本进度</th><th className="px-4 py-3">费用</th><th className="px-4 py-3">操作</th></tr></thead><tbody className="divide-y divide-border-subtle">{runs.map(run => <tr key={run.run_id}><td className="px-4 py-3">{run.suite_name || run.suite_key || 'RAG Suite'} <span className="text-text-muted">v{run.suite_version || '—'}</span></td><td className="px-4 py-3 font-mono text-xs">{run.run_id}</td><td className="px-4 py-3">{runStatusBadge(run)}</td><td className="px-4 py-3">{gateBadge(run)}</td><td className="px-4 py-3 tabular-nums">{run.completed_cases} / {run.total_cases} · 通过 {run.passed_cases}</td><td className="px-4 py-3 tabular-nums" aria-label={`费用：${formatEvaluationRunCost(run.cost_amount, run.cost_currency)}`}>{formatEvaluationRunCost(run.cost_amount, run.cost_currency)}</td><td className="px-4 py-3"><button type="button" onClick={() => setSelectedRunId(run.run_id)} className="mr-3 font-semibold text-accent hover:underline">逐题与 Trace</button>{run.job_status === 'failed' ? <button type="button" disabled={busy} onClick={() => void retry(run)} className="font-semibold text-accent disabled:opacity-50">恢复</button> : !terminal.has(run.job_status) && <button type="button" disabled={busy} onClick={() => void cancel(run)} className="text-red-700 disabled:opacity-50">取消</button>}</td></tr>)}</tbody></table>
      {!runs.length && <EvalEmpty>暂无 V2 运行。</EvalEmpty>}
    </EvalCard>

    {selectedRun && <div className="mt-4"><EvalCard
      title={'逐题样本 · ' + selectedRun.run_id.slice(0, 8)}
      subtitle="每条结果绑定到运行快照中的 case revision；Token 仅代表答案生成阶段。人工评分使用冻结 Rubric 单独留痕，不回写机器分数或 Gate。"
      bodyClassName="overflow-x-auto p-0"
    >
      <table className="w-full min-w-[650px] text-left text-sm"><thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">Case</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">耗时</th><th className="px-4 py-3">评分指标 / Token</th><th className="px-4 py-3">详情与复核</th></tr></thead><tbody className="divide-y divide-border-subtle">{results.map(result => <tr key={result.result_id}><td className="px-4 py-3 font-mono text-xs">{result.case_id} · r{result.case_revision}</td><td className="px-4 py-3">{result.status === 'pass' ? <EvalBadge text="通过" tone="good" /> : result.status === 'fail' ? <EvalBadge text="未通过" tone="bad" /> : <EvalBadge text={result.status === 'error' ? '执行错误' : '跳过'} tone="warn" />}{result.failure_stage ? <span className="ml-2 text-text-muted">{result.failure_stage}</span> : null}</td><td className="px-4 py-3 tabular-nums">{typeof result.latency_ms === 'number' ? `${result.latency_ms} ms` : '—'}</td><td className="px-4 py-3 text-xs text-text-secondary">{Object.entries(result.scores || {}).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1])).map(([key, value]) => `${scoreLabels[key] || key}: ${value}`).join(' · ') || '无数值评分'}</td><td className="px-4 py-3"><EvaluationV2RunCaseDetailButton runId={selectedRun.run_id} caseId={result.case_id} />{selectedRun.job_status === 'completed' && !['sql', 'cs', 'e2e'].includes(selectedRun.module || '') && (result.status === 'pass' || result.status === 'fail') && <div className="mt-2"><EvaluationHumanReviewButton runId={selectedRun.run_id} caseId={result.case_id} /></div>}</td></tr>)}</tbody></table>
      {selectedRun.job_status === 'completed' && results.length === 0 && <EvalEmpty>本次运行没有可查看的逐例结果。</EvalEmpty>}
    </EvalCard></div>}
  </div>
}
