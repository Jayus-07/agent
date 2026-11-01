'use client'

// cs-workstation-v2 P6：质检中心（/cs/qa）。
// 三 Tab 对齐原型 agent-workstation.html：
//   抽检任务（全角色：agent=我的任务 / supervisor=全部）——选中加载、AI 预填
//   建议（source=ai，请人工终审）、四维拖分实时合计 /100、保存草稿/提交评分
//   （提交改状态）、被检坐席本人可对已评分任务发起申诉；
//   申诉处理（supervisor）——维持 / 改判（终审分+结论+意见必填），终态锁定；
//   质检日报（supervisor）——近 7 天抽检聚合（含人工抽检数据）。
import { useCallback, useEffect, useState } from 'react'
import {
  appealQaScore,
  decideQaAppeal,
  getMyStatus,
  getQaReport,
  listQaTasks,
  requestQaAiPrefill,
  submitQaScore,
  type QaDimKey,
  type QaReportRow,
  type QaScoreInput,
  type QaTask,
} from '@/cs2/workspace/api'

const DIMENSIONS: Array<{ key: QaDimKey; label: string }> = [
  { key: 'service', label: '服务规范' },
  { key: 'accuracy', label: '准确性' },
  { key: 'compliance', label: '合规' },
  { key: 'timeliness', label: '处理时效' },
]

const STATUS_ZH: Record<string, string> = {
  pending: '待评分',
  draft: '草稿',
  scored: '已评分',
}

const APPEAL_ZH: Record<string, string> = {
  none: '—',
  appealing: '申诉中',
  upheld: '已维持',
  overturned: '已改判',
}

function statusBadgeClass(status: string): string {
  if (status === 'scored') return 'bg-emerald-50 text-emerald-600'
  if (status === 'draft') return 'bg-slate-100 text-slate-500'
  return 'bg-amber-50 text-amber-600'
}

function verdictBadgeClass(verdict: string | null): string {
  if (verdict === 'pass') return 'bg-emerald-50 text-emerald-600'
  if (verdict === 'fail') return 'bg-red-50 text-red-600'
  return 'bg-slate-100 text-slate-400'
}

function fmtBatch(batch: string | null): string {
  if (!batch) return '—'
  const parts = batch.split('-')
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : batch
}

type Tab = 'tasks' | 'appeal' | 'report'

export default function Cs2QaPage() {
  const [csRole, setCsRole] = useState<string | null>(null)
  const [meAgentId, setMeAgentId] = useState('')
  const [tab, setTab] = useState<Tab>('tasks')
  const [statusFilter, setStatusFilter] = useState('')
  const [tasks, setTasks] = useState<QaTask[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  // 评分表单（四维 0-25 + 结论 + 评语）
  const [dims, setDims] = useState({ service: 0, accuracy: 0, compliance: 0, timeliness: 0 })
  const [verdict, setVerdict] = useState<'pass' | 'fail'>('pass')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 申诉（被检坐席本人发起）
  const [appealReason, setAppealReason] = useState('')

  // 终审（supervisor：维持/改判面板）
  const [decideTaskId, setDecideTaskId] = useState<number | null>(null)
  const [finalScore, setFinalScore] = useState('')
  const [finalVerdict, setFinalVerdict] = useState<'pass' | 'fail'>('pass')
  const [decisionOpinion, setDecisionOpinion] = useState('')

  const [report, setReport] = useState<QaReportRow[]>([])

  const isSupervisor = csRole === 'supervisor'

  const reload = useCallback(async (status: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await listQaTasks(status ? { status } : undefined)
      setTasks(data.items ?? [])
    } catch {
      setError('抽检任务加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getMyStatus()
      .then((status) => {
        if (cancelled) return
        setCsRole(String(status.cs_role ?? 'agent'))
        setMeAgentId(String(status.agent_id ?? ''))
      })
      .catch(() => {
        if (!cancelled) setCsRole('agent')
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    void reload(statusFilter)
  }, [reload, statusFilter])

  const loadAppeals = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listQaTasks({ appeal: 'appealing' })
      setTasks(data.items ?? [])
    } catch {
      setError('申诉列表加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadReport = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getQaReport(7)
      setReport(data.items ?? [])
    } catch {
      setError('质检日报加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  const switchTab = useCallback(
    (next: Tab) => {
      setTab(next)
      setNotice(null)
      setDecideTaskId(null)
      if (next === 'tasks') void reload(statusFilter)
      if (next === 'appeal') void loadAppeals()
      if (next === 'report') void loadReport()
    },
    [loadAppeals, loadReport, reload, statusFilter],
  )

  const selected = tasks.find((item) => item.id === selectedId) ?? null
  const locked =
    !!selected &&
    (selected.status === 'scored' || selected.appeal_status !== 'none')

  const selectTask = useCallback((task: QaTask) => {
    setSelectedId(task.id)
    setNotice(null)
    setAppealReason('')
    if (task.status === 'draft' && task.scores.service != null) {
      setDims({
        service: task.scores.service ?? 0,
        accuracy: task.scores.accuracy ?? 0,
        compliance: task.scores.compliance ?? 0,
        timeliness: task.scores.timeliness ?? 0,
      })
      if (task.verdict) setVerdict(task.verdict)
      setNote(task.note ?? '')
    } else {
      setDims({ service: 0, accuracy: 0, compliance: 0, timeliness: 0 })
      setVerdict('pass')
      setNote('')
    }
  }, [])

  const applyAiPrefill = useCallback(async () => {
    if (!selected || locked) return
    try {
      const result = await requestQaAiPrefill(selected.id)
      const prefill = result.task.ai_prefill
      if (prefill) {
        setDims({
          service: prefill.scores.service,
          accuracy: prefill.scores.accuracy,
          compliance: prefill.scores.compliance,
          timeliness: prefill.scores.timeliness,
        })
        setVerdict(prefill.verdict)
        setNote(prefill.note)
        setNotice(
          `AI 预填完成（${prefill.total} 分 · ${prefill.verdict === 'pass' ? '合格' : '不合格'}），请人工终审`,
        )
      }
    } catch {
      setNotice('AI 预填失败，请重试')
    }
  }, [locked, selected])

  const total = dims.service + dims.accuracy + dims.compliance + dims.timeliness

  const submit = useCallback(
    async (draft: boolean) => {
      if (!selected || submitting) return
      setSubmitting(true)
      setNotice(null)
      try {
        const result = await submitQaScore(selected.id, {
          ...dims,
          verdict,
          note: note.trim() || undefined,
          draft,
        })
        setNotice(
          draft
            ? '草稿已保存'
            : `评分已提交：${result.task.total_score} 分 · ${result.task.verdict === 'pass' ? '合格' : '不合格'}`,
        )
        await reload(statusFilter)
      } catch {
        setNotice('提交失败：任务可能已评分或状态变化，请刷新后重试')
        await reload(statusFilter)
      } finally {
        setSubmitting(false)
      }
    },
    [dims, note, reload, selected, statusFilter, submitting, verdict],
  )

  const fileAppeal = useCallback(async () => {
    if (!selected || !appealReason.trim() || submitting) return
    setSubmitting(true)
    setNotice(null)
    try {
      await appealQaScore(selected.id, appealReason.trim())
      setNotice('申诉已提交，等待主管终审')
      setAppealReason('')
      await reload(statusFilter)
    } catch {
      setNotice('申诉提交失败：可能已申诉或状态变化，请刷新后重试')
      await reload(statusFilter)
    } finally {
      setSubmitting(false)
    }
  }, [appealReason, reload, selected, statusFilter, submitting])

  const decide = useCallback(
    async (decision: 'upheld' | 'overturned') => {
      if (!decideTaskId || submitting) return
      if (decision === 'overturned' && finalScore === '') return
      setSubmitting(true)
      setNotice(null)
      try {
        await decideQaAppeal(decideTaskId, {
          decision,
          opinion: decisionOpinion.trim(),
          final_score:
            decision === 'overturned' ? Number(finalScore) : undefined,
          final_verdict: decision === 'overturned' ? finalVerdict : undefined,
        })
        setNotice(decision === 'upheld' ? '已维持原评分' : '已改判，终审分生效')
        setDecideTaskId(null)
        setDecisionOpinion('')
        setFinalScore('')
        await loadAppeals()
      } catch {
        setNotice('终审提交失败：申诉可能已被处理，请刷新后重试')
        await loadAppeals()
      } finally {
        setSubmitting(false)
      }
    },
    [
      decideTaskId,
      decisionOpinion,
      finalScore,
      finalVerdict,
      loadAppeals,
      submitting,
    ],
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <h1 className="text-sm font-semibold text-slate-900">质检中心</h1>
        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">
          agent：我的任务 · supervisor：全部与终审
        </span>
        {notice && (
          <span className="ml-auto text-[11px] text-slate-500" role="status">
            {notice}
          </span>
        )}
      </div>

      <div className="flex flex-none gap-1 border-b border-slate-200 bg-white px-4 py-2">
        <button
          type="button"
          className={
            tab === 'tasks'
              ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
              : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
          }
          onClick={() => switchTab('tasks')}
        >
          抽检任务
        </button>
        {isSupervisor && (
          <button
            type="button"
            className={
              tab === 'appeal'
                ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
                : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
            }
            onClick={() => switchTab('appeal')}
          >
            申诉处理
          </button>
        )}
        {isSupervisor && (
          <button
            type="button"
            className={
              tab === 'report'
                ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
                : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
            }
            onClick={() => switchTab('report')}
          >
            质检日报
          </button>
        )}
      </div>

      {tab === 'tasks' && (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-none gap-1 px-4 py-2">
            {[
              { key: '', label: '全部' },
              { key: 'pending', label: '待评分' },
              { key: 'draft', label: '草稿' },
              { key: 'scored', label: '已评分' },
            ].map((chip) => (
              <button
                key={chip.key}
                type="button"
                className={
                  statusFilter === chip.key
                    ? 'rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-white'
                    : 'rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-200'
                }
                onClick={() => setStatusFilter(chip.key)}
              >
                {chip.label}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">会话</th>
                  <th className="px-2 py-2 font-medium">坐席</th>
                  <th className="px-2 py-2 font-medium">批次</th>
                  <th className="px-2 py-2 font-medium">状态</th>
                  <th className="px-2 py-2 font-medium">得分 / 结论</th>
                  <th className="px-2 py-2 font-medium">申诉</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr>
                    <td className="px-4 py-3 text-slate-400" colSpan={6}>
                      加载中…
                    </td>
                  </tr>
                )}
                {!loading && error && (
                  <tr>
                    <td className="px-4 py-3 text-red-500" colSpan={6}>
                      {error}
                    </td>
                  </tr>
                )}
                {!loading && !error && tasks.length === 0 && (
                  <tr>
                    <td className="px-4 py-3 text-slate-400" colSpan={6}>
                      暂无抽检任务
                    </td>
                  </tr>
                )}
                {tasks.map((task) => (
                  <tr
                    key={task.id}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${
                      selectedId === task.id ? 'bg-indigo-50/60' : ''
                    }`}
                  >
                    <td
                      className="px-4 py-2 font-medium text-slate-900"
                      onClick={() => selectTask(task)}
                    >
                      #{task.conversation_id.slice(0, 8)}
                    </td>
                    <td
                      className="px-2 py-2 text-slate-600"
                      onClick={() => selectTask(task)}
                    >
                      {task.agent_id}
                    </td>
                    <td
                      className="px-2 py-2 text-slate-500"
                      onClick={() => selectTask(task)}
                    >
                      {fmtBatch(task.batch_date)}
                    </td>
                    <td className="px-2 py-2" onClick={() => selectTask(task)}>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] ${statusBadgeClass(task.status)}`}
                      >
                        {STATUS_ZH[task.status] ?? task.status}
                      </span>
                    </td>
                    <td
                      className="px-2 py-2 text-slate-600"
                      onClick={() => selectTask(task)}
                    >
                      {task.total_score != null ? (
                        <span className="flex items-center gap-1">
                          <b>{task.total_score}</b>·
                          <span
                            className={`rounded-full px-1.5 py-0.5 text-[10px] ${verdictBadgeClass(task.verdict)}`}
                          >
                            {task.verdict === 'pass' ? '合格' : '不合格'}
                          </span>
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="px-2 py-2 text-slate-500">
                      {APPEAL_ZH[task.appeal_status] ?? task.appeal_status}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="flex-none border-t border-slate-200 bg-white p-4">
              <div className="mx-auto max-w-xl space-y-3">
                <div className="flex items-center gap-2">
                  <h3 className="text-xs font-semibold text-slate-900">
                    评分：会话 #{selected.conversation_id.slice(0, 8)} ·{' '}
                    {selected.agent_id}
                    {selected.status === 'scored' ? '（已提交）' : ''}
                  </h3>
                  {!locked && (
                    <button
                      type="button"
                      className="ml-auto rounded-lg border border-indigo-200 px-2 py-1 text-[11px] text-indigo-600 hover:bg-indigo-50"
                      onClick={() => void applyAiPrefill()}
                    >
                      ✦ AI 预填建议
                    </button>
                  )}
                </div>

                {DIMENSIONS.map((dim) => (
                  <div key={dim.key} className="flex items-center gap-2 text-xs">
                    <span className="w-16 text-slate-600">{dim.label}</span>
                    <input
                      type="range"
                      min={0}
                      max={25}
                      value={dims[dim.key]}
                      aria-label={`${dim.label}得分`}
                      disabled={locked || submitting}
                      onChange={(e) =>
                        setDims((prev) => ({
                          ...prev,
                          [dim.key]: Number(e.target.value),
                        }))
                      }
                      className="flex-1 accent-indigo-600"
                    />
                    <b className="w-6 text-right text-slate-900">
                      {dims[dim.key]}
                    </b>
                    <span className="text-slate-400">/25</span>
                  </div>
                ))}
                <div className="flex items-center gap-2 border-t border-slate-100 pt-2 text-xs">
                  <span className="w-16 text-slate-600">合计</span>
                  <b className="text-base text-indigo-600">{total}</b>
                  <span className="text-slate-400">/100</span>
                </div>

                <div
                  className="flex items-center gap-4 text-xs"
                  role="radiogroup"
                  aria-label="结论"
                >
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      name="qa-verdict"
                      checked={verdict === 'pass'}
                      disabled={locked || submitting}
                      onChange={() => setVerdict('pass')}
                    />
                    合格
                  </label>
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      name="qa-verdict"
                      checked={verdict === 'fail'}
                      disabled={locked || submitting}
                      onChange={() => setVerdict('fail')}
                    />
                    不合格
                  </label>
                </div>

                <textarea
                  aria-label="评语"
                  className="min-h-16 w-full resize-none rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs outline-none focus:border-indigo-500"
                  placeholder="评语：扣分原因、改进建议…"
                  value={note}
                  disabled={locked || submitting}
                  onChange={(e) => setNote(e.target.value)}
                />

                {locked ? (
                  <div className="rounded-lg bg-slate-50 p-2.5 text-[11px] text-slate-600">
                    {selected.appeal_status === 'none' ? (
                      '评分已提交（终态）。如对评分有异议，可发起申诉。'
                    ) : (
                      <>
                        申诉状态：{APPEAL_ZH[selected.appeal_status]}
                        {selected.appeal_decision === 'overturned' &&
                          selected.final_score != null &&
                          ` · 终审 ${selected.final_score} 分 · ${selected.final_verdict === 'pass' ? '合格' : '不合格'}`}
                        {selected.appeal_opinion
                          ? ` · 意见：${selected.appeal_opinion}`
                          : ''}
                      </>
                    )}
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                      disabled={submitting}
                      onClick={() => void submit(true)}
                    >
                      保存草稿
                    </button>
                    <button
                      type="button"
                      className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-40"
                      disabled={submitting}
                      onClick={() => void submit(false)}
                    >
                      {submitting ? '提交中…' : '提交评分'}
                    </button>
                  </div>
                )}

                {/* 被检坐席本人对已评分任务发起申诉 */}
                {selected.status === 'scored' &&
                  selected.appeal_status === 'none' &&
                  selected.agent_id === meAgentId && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-2.5">
                      <label
                        htmlFor="qa-appeal-reason"
                        className="block text-[11px] font-medium text-amber-700"
                      >
                        对该评分发起申诉（理由必填）
                      </label>
                      <textarea
                        id="qa-appeal-reason"
                        aria-label="申诉理由"
                        className="mt-1.5 min-h-12 w-full resize-none rounded-lg border border-amber-200 bg-white px-2.5 py-2 text-xs outline-none focus:border-amber-500"
                        placeholder="例如：已提前告知物流延迟，处理仍在时效内"
                        value={appealReason}
                        onChange={(e) => setAppealReason(e.target.value)}
                      />
                      <button
                        type="button"
                        className="mt-1.5 rounded-lg border border-amber-300 px-3 py-1 text-[11px] text-amber-700 hover:bg-amber-100 disabled:opacity-40"
                        disabled={!appealReason.trim() || submitting}
                        onClick={() => void fileAppeal()}
                      >
                        提交申诉
                      </button>
                    </div>
                  )}
              </div>
            </div>
          )}
          {!selected && (
            <div className="flex-none border-t border-slate-200 bg-white px-4 py-3 text-xs text-slate-400">
              ↑ 选择上方抽检任务进行评分（AI 预填建议仅供人工终审参考）
            </div>
          )}
        </div>
      )}

      {tab === 'appeal' && isSupervisor && (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">评分</th>
                <th className="px-2 py-2 font-medium">会话</th>
                <th className="px-2 py-2 font-medium">申诉人</th>
                <th className="px-2 py-2 font-medium">申诉理由</th>
                <th className="px-2 py-2 font-medium">状态</th>
                <th className="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td className="px-4 py-3 text-slate-400" colSpan={6}>
                    加载中…
                  </td>
                </tr>
              )}
              {!loading && error && (
                <tr>
                  <td className="px-4 py-3 text-red-500" colSpan={6}>
                    {error}
                  </td>
                </tr>
              )}
              {!loading && !error && tasks.length === 0 && (
                <tr>
                  <td className="px-4 py-3 text-slate-400" colSpan={6}>
                    暂无申诉任务
                  </td>
                </tr>
              )}
              {tasks.map((task) => (
                <tr key={task.id} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {task.total_score != null ? `${task.total_score} 分` : '—'}
                  </td>
                  <td className="px-2 py-2 text-slate-600">
                    #{task.conversation_id.slice(0, 8)}
                  </td>
                  <td className="px-2 py-2 text-slate-600">
                    {task.appeal_requested_by ?? task.agent_id}
                  </td>
                  <td
                    className="max-w-56 truncate px-2 py-2 text-slate-500"
                    title={task.appeal_reason ?? ''}
                  >
                    {task.appeal_reason ?? '—'}
                  </td>
                  <td className="px-2 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] ${
                        task.appeal_status === 'appealing'
                          ? 'bg-amber-50 text-amber-600'
                          : task.appeal_status === 'upheld'
                            ? 'bg-emerald-50 text-emerald-600'
                            : 'bg-blue-50 text-blue-600'
                      }`}
                    >
                      {APPEAL_ZH[task.appeal_status] ?? task.appeal_status}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    {task.appeal_status === 'appealing' &&
                    decideTaskId !== task.id ? (
                      <button
                        type="button"
                        className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                        onClick={() => {
                          setDecideTaskId(task.id)
                          setFinalScore(String(task.total_score ?? ''))
                          setFinalVerdict(task.verdict ?? 'pass')
                          setDecisionOpinion('')
                        }}
                      >
                        终审
                      </button>
                    ) : decideTaskId === task.id ? (
                      <span className="flex flex-col gap-1.5">
                        <span className="flex items-center gap-1">
                          <input
                            type="number"
                            min={0}
                            max={100}
                            aria-label="终审得分"
                            placeholder="终审分"
                            className="w-16 rounded border border-slate-200 px-1.5 py-1 text-[11px]"
                            value={finalScore}
                            onChange={(e) => setFinalScore(e.target.value)}
                          />
                          <select
                            aria-label="终审结论"
                            className="rounded border border-slate-200 px-1 py-1 text-[11px]"
                            value={finalVerdict}
                            onChange={(e) =>
                              setFinalVerdict(
                                e.target.value as 'pass' | 'fail',
                              )
                            }
                          >
                            <option value="pass">合格</option>
                            <option value="fail">不合格</option>
                          </select>
                        </span>
                        <input
                          type="text"
                          aria-label="终审意见"
                          placeholder="终审意见（必填）"
                          className="w-48 rounded border border-slate-200 px-1.5 py-1 text-[11px]"
                          value={decisionOpinion}
                          onChange={(e) => setDecisionOpinion(e.target.value)}
                        />
                        <span className="flex gap-1">
                          <button
                            type="button"
                            className="rounded-lg bg-blue-600 px-2 py-1 text-[11px] text-white disabled:opacity-40"
                            disabled={
                              submitting ||
                              !decisionOpinion.trim() ||
                              finalScore === ''
                            }
                            onClick={() => void decide('overturned')}
                          >
                            改判
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-emerald-200 px-2 py-1 text-[11px] text-emerald-600 disabled:opacity-40"
                            disabled={submitting || !decisionOpinion.trim()}
                            onClick={() => void decide('upheld')}
                          >
                            维持
                          </button>
                          <button
                            type="button"
                            className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-500"
                            onClick={() => setDecideTaskId(null)}
                          >
                            取消
                          </button>
                        </span>
                      </span>
                    ) : (
                      <span className="text-slate-400">
                        {task.appeal_decision === 'overturned' &&
                        task.final_score != null
                          ? `终审 ${task.final_score} 分`
                          : '—'}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'report' && isSupervisor && (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">日期</th>
                <th className="px-2 py-2 font-medium">抽检数</th>
                <th className="px-2 py-2 font-medium">已评分</th>
                <th className="px-2 py-2 font-medium">合格率</th>
                <th className="px-2 py-2 font-medium">平均分</th>
                <th className="px-2 py-2 font-medium">主要扣分项</th>
                <th className="px-2 py-2 font-medium">申诉</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td className="px-4 py-3 text-slate-400" colSpan={7}>
                    加载中…
                  </td>
                </tr>
              )}
              {!loading && error && (
                <tr>
                  <td className="px-4 py-3 text-red-500" colSpan={7}>
                    {error}
                  </td>
                </tr>
              )}
              {!loading && !error && report.length === 0 && (
                <tr>
                  <td className="px-4 py-3 text-slate-400" colSpan={7}>
                    近 7 天暂无抽检数据
                  </td>
                </tr>
              )}
              {report.map((row) => (
                <tr key={row.batch_date} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {fmtBatch(row.batch_date)}
                  </td>
                  <td className="px-2 py-2 text-slate-600">{row.sampled}</td>
                  <td className="px-2 py-2 text-slate-600">{row.scored}</td>
                  <td className="px-2 py-2 text-slate-600">
                    {row.pass_rate != null
                      ? `${(row.pass_rate * 100).toFixed(1)}%`
                      : '—'}
                  </td>
                  <td className="px-2 py-2 text-slate-600">
                    {row.avg_score != null ? row.avg_score : '—'}
                  </td>
                  <td className="px-2 py-2 text-slate-500">
                    {row.top_deduction ?? '—'}
                  </td>
                  <td className="px-2 py-2 text-slate-600">{row.appeals}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
