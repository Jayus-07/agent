'use client'

import { useEffect, useRef, useState } from 'react'
import {
  evaluationService,
  type EvaluationV2Gate,
  type EvaluationV2GateApproval,
  type EvaluationV2Run,
} from '@/api/evaluation'
import { atLeast } from '@/lib/auth'
import { createIdempotencyKey } from '@/api/client'

function verdictLabel(value: string | null | undefined): string {
  if (value === 'PASS') return 'PASS · 通过'
  if (value === 'FAIL') return 'FAIL · 未通过'
  if (value === 'INVALID') return 'INVALID · 无效'
  return '尚无裁决'
}

function decisionLabel(value: EvaluationV2GateApproval['decision']): string {
  return value === 'approved' ? '批准放行' : '拒绝放行'
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

interface PendingApproval {
  requestId: string
  gateDecisionId: string
  decision: 'approved' | 'rejected'
  releaseReference: string
  rationale: string
}

export default function EvaluationReleaseQualityPanel() {
  const [runs, setRuns] = useState<EvaluationV2Run[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [gate, setGate] = useState<EvaluationV2Gate | null>(null)
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([])
  const [releaseReference, setReleaseReference] = useState('')
  const [rationale, setRationale] = useState('')
  const [loading, setLoading] = useState(true)
  const [savingApproval, setSavingApproval] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const pendingApproval = useRef<PendingApproval | null>(null)

  const load = async () => {
    if (!atLeast('admin')) { setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const items = await evaluationService.listV2Runs(100)
      setRuns(items)
      const selected = items.find(item => item.run_id === selectedId) || items[0]
      setSelectedId(selected?.run_id || '')
      if (selected) {
        const [decision, events] = await Promise.all([
          evaluationService.getV2Gate(selected.run_id),
          evaluationService.listV2AuditEvents({ target_type: 'run', target_id: selected.run_id, limit: 50 }),
        ])
        setGate(decision)
        setAudit(events)
      } else {
        setGate(null)
        setAudit([])
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取 V2 Gate 与审计记录失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const selectRun = async (runId: string) => {
    setSelectedId(runId)
    setGate(null)
    setError('')
    setMessage('')
    pendingApproval.current = null
    try {
      const [decision, events] = await Promise.all([
        evaluationService.getV2Gate(runId),
        evaluationService.listV2AuditEvents({ target_type: 'run', target_id: runId, limit: 50 }),
      ])
      setGate(decision)
      setAudit(events)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取运行 Gate 失败')
    }
  }

  const submitApproval = async (decision: 'approved' | 'rejected') => {
    const latest = gate?.decisions[0]
    const normalizedReference = releaseReference.trim()
    const normalizedRationale = rationale.trim()
    if (!selectedId || !latest || gate?.verdict !== 'PASS') {
      setError('当前运行没有可审批的 PASS Gate 裁决。')
      return
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._:/@+#-]{2,159}$/.test(normalizedReference)) {
      setError('请填写 3 到 160 位英文、数字或 . _ : / @ + # - 组成的发布引用。')
      return
    }
    if (normalizedRationale.length < 8 || normalizedRationale.length > 2000) {
      setError('审批理由需为 8 到 2000 个非空字符。')
      return
    }

    const previous = pendingApproval.current
    const pending = previous
      && previous.gateDecisionId === latest.decision_id
      && previous.decision === decision
      && previous.releaseReference === normalizedReference
      && previous.rationale === normalizedRationale
      ? previous
      : {
        requestId: createIdempotencyKey(),
        gateDecisionId: latest.decision_id,
        decision,
        releaseReference: normalizedReference,
        rationale: normalizedRationale,
      }
    pendingApproval.current = pending
    setSavingApproval(true)
    setError('')
    setMessage('')
    try {
      const created = await evaluationService.createV2GateApproval(selectedId, {
        gate_decision_id: pending.gateDecisionId,
        request_id: pending.requestId,
        decision: pending.decision,
        release_reference: pending.releaseReference,
        rationale: pending.rationale,
      })
      pendingApproval.current = null
      setGate(current => current
        ? { ...current, approvals: [created, ...current.approvals.filter(item => item.approval_id !== created.approval_id)] }
        : current)
      setMessage(`审批记录已追加：${decisionLabel(created.decision)} · ${created.release_reference}。该操作没有触发 CI 或部署。`)
      try {
        setAudit(await evaluationService.listV2AuditEvents({ target_type: 'run', target_id: selectedId, limit: 50 }))
      } catch {
        setError('审批已记录；审计列表刷新失败，请稍后刷新页面。')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交发布审批失败')
    } finally {
      setSavingApproval(false)
    }
  }

  if (!atLeast('admin')) return <p className="mx-auto max-w-7xl p-6 text-sm text-text-secondary">V2 Gate 与审计记录仅对管理角色开放。</p>
  const passed = runs.filter(run => run.gate_verdict === 'PASS').length
  const failed = runs.filter(run => run.gate_verdict === 'FAIL').length
  const invalid = runs.filter(run => run.gate_verdict === 'INVALID').length
  const selected = runs.find(run => run.run_id === selectedId)
  const canApprove = atLeast('super_admin') && gate?.verdict === 'PASS' && Boolean(gate.decisions[0]?.decision_id)

  return <div className="mx-auto max-w-7xl space-y-5 px-6 py-6">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold text-text-primary">V2 发布质量与 Gate</h2>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-text-secondary">Gate 决策从服务端追加式记录读取；任务成功、质量通过与运行有效性分别展示。人工审批绑定具体 PASS 裁决，只记意见，不触发 CI 或部署。</p>
      </div>
      <button type="button" onClick={() => void load()} disabled={loading} className="min-h-10 rounded-md border border-border-subtle bg-surface-base px-3 text-sm">刷新</button>
    </div>
    {error && <p className="rounded-md border border-red-200 bg-surface-base p-3 text-sm text-red-800" role="alert">{error}</p>}
    {message && <p className="rounded-md border border-border-subtle bg-surface-base p-3 text-sm text-text-secondary" role="status">{message}</p>}

    <div className="grid gap-3 sm:grid-cols-3">
      <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">Gate 通过</p><p className="mt-2 text-2xl font-semibold tabular-nums text-emerald-700">{passed}</p></article>
      <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">Gate 未通过</p><p className="mt-2 text-2xl font-semibold tabular-nums text-red-700">{failed}</p></article>
      <article className="rounded-lg border border-border-subtle bg-surface-base p-4"><p className="text-sm text-text-secondary">无效运行</p><p className="mt-2 text-2xl font-semibold tabular-nums text-amber-800">{invalid}</p></article>
    </div>

    <section className="overflow-x-auto rounded-lg border border-border-subtle bg-surface-base" aria-labelledby="evaluation-v2-gates-heading">
      <div className="border-b border-border-subtle p-4">
        <h3 id="evaluation-v2-gates-heading" className="font-semibold text-text-primary">运行裁决</h3>
        <p className="mt-1 text-xs text-text-muted">记录缺失时显示“尚无裁决”，不会由页面代算 PASS。</p>
      </div>
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="bg-bg-elevated text-xs text-text-secondary"><tr><th className="px-4 py-3">Run</th><th className="px-4 py-3">Suite</th><th className="px-4 py-3">任务状态</th><th className="px-4 py-3">Gate</th><th className="px-4 py-3">通过样本</th><th className="px-4 py-3">详情</th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {runs.map(run => <tr key={run.run_id}>
            <td className="px-4 py-3 font-mono text-xs">{run.run_id}</td>
            <td className="px-4 py-3">{run.suite_name || run.suite_key || 'RAG Suite'} · v{run.suite_version || '—'}</td>
            <td className="px-4 py-3">{run.job_status}</td>
            <td className="px-4 py-3">{verdictLabel(run.gate_verdict)}</td>
            <td className="px-4 py-3 tabular-nums">{run.passed_cases} / {run.total_cases}</td>
            <td className="px-4 py-3"><button type="button" onClick={() => void selectRun(run.run_id)} className="text-accent">查看原因</button></td>
          </tr>)}
        </tbody>
      </table>
      {!loading && !runs.length && <p className="p-8 text-center text-sm text-text-secondary">暂无 V2 运行裁决。</p>}
    </section>

    {selected && <section className="grid gap-4 lg:grid-cols-2">
      <article className="rounded-lg border border-border-subtle bg-surface-base p-4">
        <h3 className="font-semibold text-text-primary">裁决原因 · {verdictLabel(gate?.verdict)}</h3>
        {gate?.reasons?.length
          ? <ul className="mt-3 space-y-2 text-sm text-text-secondary">{gate.reasons.map((reason, index) => <li key={index} className="rounded bg-bg-elevated p-3"><code className="text-xs">{String(reason.code || 'reason')}</code><pre className="mt-1 whitespace-pre-wrap break-words text-xs">{JSON.stringify(reason, null, 2)}</pre></li>)}</ul>
          : <p className="mt-3 text-sm text-text-secondary">{gate ? '当前没有 Gate 原因记录。' : '正在读取服务端裁决…'}</p>}
      </article>
      <article className="rounded-lg border border-border-subtle bg-surface-base p-4">
        <h3 className="font-semibold text-text-primary">不可变审计事件</h3>
        {audit.length
          ? <ol className="mt-3 space-y-3">{audit.map((event, index) => <li key={String(event.event_id || index)} className="border-l-2 border-border-subtle pl-3"><p className="text-sm font-medium text-text-primary">{String(event.action || '操作')} · {String(event.actor_user_id || '未知操作者')}</p><p className="mt-1 text-xs text-text-muted">{String(event.created_at || '')}</p><pre className="mt-1 whitespace-pre-wrap break-words text-xs text-text-secondary">{JSON.stringify(event.details_json || {}, null, 2)}</pre></li>)}</ol>
          : <p className="mt-3 text-sm text-text-secondary">当前运行没有审计事件。</p>}
      </article>
    </section>}

    {selected && <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="evaluation-gate-approval-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 id="evaluation-gate-approval-heading" className="font-semibold text-text-primary">人工发布审批</h3><p className="mt-1 text-sm text-text-secondary">审批记录与当前 PASS Gate 裁决绑定，保留发布引用和理由；系统不会据此自动发布。</p></div>
        <span className="rounded-full bg-bg-elevated px-2.5 py-1 text-xs text-text-secondary">{gate?.approvals.length || 0} 条审批记录</span>
      </div>
      {canApprove
        ? <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm font-medium text-text-primary">发布引用<input aria-label="发布引用" value={releaseReference} onChange={event => setReleaseReference(event.target.value)} maxLength={160} placeholder="例如 release:v2.4.1 或 commit:abc1234" className="mt-2 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm" /></label>
          <label className="text-sm font-medium text-text-primary md:row-span-2">审批理由<textarea aria-label="审批理由" value={rationale} onChange={event => setRationale(event.target.value)} maxLength={2000} minLength={8} rows={3} placeholder="说明审批依据、风险检查和发布范围" className="mt-2 w-full rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm" /></label>
          <div className="flex flex-wrap items-start gap-2">
            <button type="button" onClick={() => void submitApproval('approved')} disabled={savingApproval} className="min-h-10 rounded-md bg-accent px-4 text-sm font-medium text-white disabled:opacity-50">{savingApproval ? '提交中…' : '批准放行'}</button>
            <button type="button" onClick={() => void submitApproval('rejected')} disabled={savingApproval} className="min-h-10 rounded-md border border-border-subtle px-4 text-sm font-medium text-text-primary disabled:opacity-50">拒绝放行</button>
          </div>
          <p className="text-xs text-text-muted md:col-span-2">当前绑定裁决：{gate?.decisions[0]?.decision_id || '—'} · 操作需超级管理员权限。</p>
        </div>
        : <p className="mt-4 rounded-md bg-bg-elevated p-3 text-sm text-text-secondary">{gate?.verdict !== 'PASS' ? '仅 Gate PASS 的已完成运行可以发起人工发布审批。' : '需要超级管理员权限提交发布审批。'}</p>}
      {gate?.approvals.length
        ? <ol className="mt-4 divide-y divide-border-subtle border-t border-border-subtle">{gate.approvals.map(approval => <li key={approval.approval_id} className="grid gap-2 py-3 text-sm sm:grid-cols-[auto_1fr_auto] sm:items-start">
          <span className={approval.decision === 'approved' ? 'font-medium text-emerald-700' : 'font-medium text-red-700'}>{decisionLabel(approval.decision)}</span>
          <div><p className="font-medium text-text-primary">{approval.release_reference}</p><p className="mt-1 text-text-secondary">{approval.rationale}</p><p className="mt-1 text-xs text-text-muted">Gate {approval.gate_decision_id} · {approval.decided_by}</p></div>
          <time className="text-xs text-text-muted">{formatDate(approval.decided_at)}</time>
        </li>)}</ol>
        : <p className="mt-4 border-t border-border-subtle pt-3 text-sm text-text-secondary">当前 Gate 还没有人工审批记录。</p>}
    </section>}
  </div>
}
