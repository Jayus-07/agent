'use client'

/**
 * 运行评测 tab —— 严格按已定稿原型实现：
 * 发起表单（数据集/评测方式/被测 Prompt/知识库快照）→ 运行列表（来源/方式/进度）→ 环境指纹卡。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, Play, RotateCw, X } from 'lucide-react'
import {
  evaluationService,
  type EvaluationV2Gate,
  type EvaluationSuite,
  type EvaluationV2Run,
  type EvaluationV2RunFingerprint,
} from '@/api/evaluation'
import { createIdempotencyKey } from '@/api/client'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { promptsService, type PromptListItem } from '@/api/prompts'
import { atLeast } from '@/lib/auth'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Empty, modeShort, MODE_LABELS, StatusBadge, TableWrap, Td, Th, timeShort, fmtCost } from './evalUi'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked'])

export default function RunsTab({ promptPrefill, focusRunId, onGoCompare }: { promptPrefill?: { key: string; version: string } | null; focusRunId?: string | null; onGoCompare?: (runId: string) => void }) {
  const [runs, setRuns] = useState<EvaluationV2Run[]>([])
  const [suites, setSuites] = useState<EvaluationSuite[]>([])
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [promptKeys, setPromptKeys] = useState<PromptListItem[]>([])
  const [suiteId, setSuiteId] = useState('')
  const [mode, setMode] = useState('self+ragas')
  const [promptKey, setPromptKey] = useState('')
  const [promptVersion, setPromptVersion] = useState('')
  const [kbId, setKbId] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [gateRunId, setGateRunId] = useState('')
  const [fingerprint, setFingerprint] = useState<EvaluationV2RunFingerprint | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    if (!atLeast('super_admin')) { setLoading(false); return }
    setLoading(true); setError('')
    try {
      const [runItems, suiteItems, kbCatalog, promptItems] = await Promise.all([
        evaluationService.listV2Runs(100),
        evaluationService.listSuites(),
        listKbAuthority().catch(() => ({ knowledge_bases: [] as KbAuthorityItem[] })),
        promptsService.list().catch(() => [] as PromptListItem[]),
      ])
      setRuns(runItems)
      setSuites(suiteItems.filter(item => item.source === 'evaluation_v2' && item.status === 'published' && item.suite_version_id))
      setKbs(kbCatalog.knowledge_bases.filter(item => item.active && !item.alias_for && !item.deprecated))
      setPromptKeys(promptItems)
      setSuiteId(current => suiteItems.some(item => item.suite_version_id === current && item.status === 'published')
        ? current
        : suiteItems.find(item => item.source === 'evaluation_v2' && item.status === 'published' && item.suite_version_id)?.suite_version_id || '')
      setKbId(current => kbCatalog.knowledge_bases.some(item => item.kb_id === current) ? current : kbCatalog.knowledge_bases.find(item => item.active && !item.alias_for && !item.deprecated)?.kb_id || '')
    } catch (e) { setError(e instanceof Error ? e.message : '读取运行失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!runs.length || !runs.some(run => !TERMINAL.has(run.job_status))) return
    const timer = setInterval(() => {
      void evaluationService.listV2Runs(100).then(setRuns).catch(() => undefined)
    }, 3000)
    return () => clearInterval(timer)
  }, [runs])

  useEffect(() => {
    if (!promptPrefill) return
    const version = promptPrefill.version.trim()
    if (!/^\d+$/.test(version)) return
    setPromptKey(promptPrefill.key)
    setPromptVersion(version)
  }, [promptPrefill])

  useEffect(() => {
    if (!focusRunId) return
    setSelectedRunId(focusRunId)
  }, [focusRunId])

  useEffect(() => {
    if (!selectedRunId) { setFingerprint(null); return }
    let active = true
    void evaluationService.getV2Run(selectedRunId).then(value => { if (active) setFingerprint(value.fingerprint) }).catch(() => { if (active) setFingerprint(null) })
    return () => { active = false }
  }, [selectedRunId])

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
      setSelectedRunId(result.run.run_id)
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : '创建运行失败') }
    finally { setBusy(false) }
  }

  const cancel = (run: EvaluationV2Run) => { setBusy(true)
    evaluationService.cancelV2Run(run.run_id).then(response => setMessage(response.accepted ? `已发送取消请求：${run.run_id.slice(0, 8)}` : '运行已进入终态')).then(load).catch((e: unknown) => setError(e instanceof Error ? e.message : '取消失败')).finally(() => setBusy(false))
  }
  const retry = (run: EvaluationV2Run) => { setBusy(true)
    evaluationService.retryV2Run(run.run_id).then(() => setMessage(`已请求从冻结快照恢复：${run.run_id.slice(0, 8)}`)).then(load).catch((e: unknown) => setError(e instanceof Error ? e.message : '恢复失败')).finally(() => setBusy(false))
  }

  if (!atLeast('super_admin')) return <p className="p-5 text-sm text-text-secondary">评测运行沿用任务授权，仅超级管理员可发起和查看。</p>

  return <div className="space-y-4">
    {error && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}
    {message && <p role="status" className="text-xs text-text-secondary">{message}</p>}

    <Card title="发起评测" subtitle="运行固定数据集版本与模型 / Prompt / 知识库快照；两次运行在相同口径下才可直接对比。">
      <div className="grid gap-3 lg:grid-cols-[2fr_1.4fr_auto]">
        <select aria-label="数据集" value={suiteId} onChange={e => setSuiteId(e.target.value)} className={ctl}>
          <option value="">选择已发布数据集方案</option>
          {suites.map(item => <option key={item.suite_version_id} value={item.suite_version_id}>{item.name} · {item.module} · v{item.version}（{item.case_count} 条）</option>)}
        </select>
        {isRag ? <select aria-label="评测方式" value={mode} onChange={e => setMode(e.target.value)} className={ctl}>
          {Object.entries(MODE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select> : <p className="self-center text-xs text-text-muted">离线评测（按方案执行）</p>}
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
    </Card>

    <Card title="运行记录" subtitle="失败区分执行错误与质量不达标；错误不计入通过率分母。">
      {loading ? <Empty text="正在读取运行状态…" /> : runs.length === 0 ? <Empty text="还没有运行记录。选择上方方案发起第一次评测。" /> : <TableWrap minWidth={1160}>
        <thead className="bg-bg-elevated"><tr><Th>Run</Th><Th>数据集</Th><Th>被测配置</Th><Th>评测方式</Th><Th>状态</Th><Th>Gate</Th><Th>进度 / 结果</Th><Th end>费用</Th><Th end>耗时</Th><Th>完成时间</Th><Th end>操作</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {runs.map(run => <Fragment key={run.run_id}><tr className={run.run_id === selectedRunId ? 'bg-accent-soft/40' : ''}>
            <Td mono>{run.run_id.slice(0, 8)}</Td>
            <Td><span className="block text-text-primary">{run.suite_name || run.suite_key}</span><span className="text-[11px] text-text-muted">v{run.suite_version || '—'}</span></Td>
            <Td>{(() => { const versions = run.prompt_versions || {}; return Object.keys(versions).length ? Object.entries(versions).map(([key, value]) => `${key}@${String(value)}`).join(' · ') : '默认配置' })()}</Td>
            <Td>{modeShort(run.evaluation_mode)}{run.source && run.source !== 'manual' && <span className="ml-1.5"><Badge text={run.source === 'online' ? '线上采样' : run.source === 'prompt_release' ? '发布' : run.source} tone="accent" /></span>}</Td>
            <Td><StatusBadge status={run.job_status} /></Td>
            <Td>
              {run.gate_verdict
                ? <Badge text={run.gate_verdict} tone={run.gate_verdict === 'PASS' ? 'good' : run.gate_verdict === 'FAIL' ? 'bad' : 'warn'} />
                : <span className="text-text-muted">—</span>}
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
              <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setSelectedRunId(run.run_id)}>指纹</button>
              {run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setGateRunId(current => current === run.run_id ? '' : run.run_id)}>{run.gate_verdict === 'PASS' ? 'Gate 审批' : 'Gate 详情'}</button>}
              {run.job_status === 'completed' && <button type="button" className={`${ctlGhost} mr-1`} onClick={() => onGoCompare?.(run.run_id)}>查看结果<ArrowRight size={11} className="ml-0.5 inline" aria-hidden="true" /></button>}
              {!TERMINAL.has(run.job_status) && <button type="button" className={`${ctlGhost} mr-1`} disabled={busy} onClick={() => cancel(run)}><X size={12} className="mr-0.5 inline" aria-hidden="true" />取消</button>}
              {run.job_status === 'failed' && <button type="button" className={ctl} disabled={busy} onClick={() => retry(run)}><RotateCw size={12} className="mr-1 inline" aria-hidden="true" />重试</button>}
            </Td>
          </tr>
          {gateRunId === run.run_id && <tr><td colSpan={11} className="bg-bg-elevated/50 p-3"><GateApprovalPanel runId={run.run_id} onClose={() => setGateRunId('')} onSubmitted={() => void load()} /></td></tr>}
          </Fragment>)}
        </tbody>
      </TableWrap>}
    </Card>

    {selectedRunId && fingerprint && <Card title="运行环境指纹" subtitle="发起时冻结的被测配置；同数据集版本 + 同评测方式的两次运行才可直接对比。">
      <div className="grid gap-2 text-xs text-text-secondary sm:grid-cols-2">
        <p>评测方式：<Badge text={modeShort(fingerprint.evaluation_mode) } tone="accent" /></p>
        <p>来源：{fingerprint.source && fingerprint.source !== 'manual' ? <Badge text={fingerprint.source} tone="accent" /> : '手动'}</p>
        <p>Runner：{fingerprint.runner || '—'}</p>
        <p>生成模型（eval_gen）：{fingerprint.generation_model || '未记录'}</p>
        <p>Embedding：{fingerprint.embedding_model || '—'}</p>
        <p>Rerank：{fingerprint.rerank_model || '—'}</p>
        <p>数据集版本：{fingerprint.dataset_version_id || '—'}</p>
        <p>语料快照：{fingerprint.corpus_snapshot_id || '—'}</p>
        <p className="sm:col-span-2">Prompt 版本：{(() => { const versions = fingerprint.prompt_versions || {}; return Object.keys(versions).length ? Object.entries(versions).map(([key, value]) => `${key}@${String(value)}`).join(' · ') : '未指定' })()}</p>
      </div>
    </Card>}
  </div>
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
