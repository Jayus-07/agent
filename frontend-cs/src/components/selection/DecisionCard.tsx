'use client'

/**
 * DecisionCard — 选品决策拍板卡（UX 设计 P0-③ / B1 闭环，2026-09-17）
 *
 * 五步旅程闭环的「一键拍板」环节：报告详情页底部展示该任务下全部决策
 * 留痕（版本递增、拍板徽章、表现回填状态），并提供拍板表单
 * （adopted / rejected / deferred）与事后表现回填入口。
 *
 * 语义与后端对齐（backend/app/api/routes/selection_decision.py B1 段）：
 * - 拍板 = 留痕 + 用户决策一步完成，重复拍板产生新版本（decision_version 递增）
 * - 表现回填自由键值 actual_metrics，此处提供 销量30天/评分/备注 三个常用槽位
 */

import { useCallback, useEffect, useState } from 'react'
import {
  DecisionRecord,
  UserDecision,
  selectionDecisionApi,
} from '@/api/selectionDecision'

const DECISION_OPTIONS: { value: UserDecision; label: string; badgeClass: string }[] = [
  { value: 'adopted', label: '采纳', badgeClass: 'bg-red-50 text-red-600' },
  { value: 'rejected', label: '放弃', badgeClass: 'bg-green-50 text-green-700' },
  { value: 'deferred', label: '暂缓', badgeClass: 'bg-amber-50 text-amber-700' },
]

/** 纯函数：拍板徽章文案与样式（供测试） */
export function decisionBadge(value: UserDecision | null): { text: string; className: string } {
  const opt = DECISION_OPTIONS.find((o) => o.value === value)
  return opt
    ? { text: `已拍板·${opt.label}`, className: opt.badgeClass }
    : { text: '未拍板', className: 'bg-gray-100 text-gray-500' }
}

/** 纯函数：拍板表单校验（供测试）——candidate_id 必填，返回错误文案或 null */
export function validateDecideInput(candidateId: string, decision: UserDecision): string | null {
  if (!candidateId.trim()) return '请填写候选商品标识（如 jd:10001）'
  if (!DECISION_OPTIONS.some((o) => o.value === decision)) return '请选择拍板结论'
  return null
}

/** 纯函数：表现回填表单 → actual_metrics 键值（空槽位丢弃；供测试） */
export function buildMetrics(sales30d: string, rating: string, note: string): Record<string, unknown> {
  const metrics: Record<string, unknown> = {}
  const sales = Number(sales30d)
  const score = Number(rating)
  if (sales30d.trim() !== '' && Number.isFinite(sales)) metrics.sales_30d = sales
  if (rating.trim() !== '' && Number.isFinite(score)) metrics.rating = score
  if (note.trim() !== '') metrics.note = note.trim()
  return metrics
}

export default function DecisionCard({ taskId, category }: { taskId: string; category?: string }) {
  const [decisions, setDecisions] = useState<DecisionRecord[] | null>(null)
  const [loadError, setLoadError] = useState('')

  const [candidateId, setCandidateId] = useState('')
  const [decision, setDecision] = useState<UserDecision>('adopted')
  const [submitting, setSubmitting] = useState(false)
  const [formMessage, setFormMessage] = useState('')

  // 表现回填：记录正在编辑的 decision_id 与表单值
  const [feedbackFor, setFeedbackFor] = useState<string | null>(null)
  const [sales30d, setSales30d] = useState('')
  const [rating, setRating] = useState('')
  const [note, setNote] = useState('')
  const [feedbackMessage, setFeedbackMessage] = useState('')

  const load = useCallback(async () => {
    try {
      const { decisions: list } = await selectionDecisionApi.listDecisions(taskId)
      setDecisions(list)
      setLoadError('')
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    }
  }, [taskId])

  useEffect(() => {
    load()
  }, [load])

  const handleDecide = async () => {
    const invalid = validateDecideInput(candidateId, decision)
    if (invalid) {
      setFormMessage(invalid)
      return
    }
    setSubmitting(true)
    setFormMessage('')
    try {
      await selectionDecisionApi.decide(taskId, {
        candidate_id: candidateId.trim(),
        decision,
        category: category ?? null,
      })
      setFormMessage('拍板已记录')
      setCandidateId('')
      await load()
    } catch (e) {
      setFormMessage(`拍板失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const handleFeedback = async (decisionId: string) => {
    setFeedbackMessage('')
    try {
      await selectionDecisionApi.feedback(decisionId, buildMetrics(sales30d, rating, note))
      setFeedbackMessage('表现已回填')
      setFeedbackFor(null)
      setSales30d('')
      setRating('')
      setNote('')
      await load()
    } catch (e) {
      setFeedbackMessage(`回填失败: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <section className="mt-8 rounded-xl border border-gray-200 bg-white p-5 shadow-card" aria-label="决策拍板">
      <h2 className="text-sm font-medium text-gray-900">决策拍板</h2>
      <p className="mt-1 text-xs text-gray-500">
        拍板结论写入决策留痕（decision_log），事后真实表现可回填同一记录形成闭环。
      </p>

      {loadError && <p className="mt-3 text-xs text-red-600">决策记录加载失败: {loadError}</p>}

      {decisions && decisions.length > 0 && (
        <ul className="mt-4 space-y-2">
          {decisions.map((d) => {
            const badge = decisionBadge(d.user_decision)
            return (
              <li key={d.decision_id} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-gray-700">{d.candidate_id}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] ${badge.className}`}>{badge.text}</span>
                  <span className="text-[11px] text-gray-400">
                    v{d.decision_version} · 建议 {d.recommendation} · {d.decision_at}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-gray-500">
                  {d.feedback_at ? (
                    <span>表现已回填 · {d.feedback_at}</span>
                  ) : (
                    <button
                      type="button"
                      className="text-accent hover:underline"
                      onClick={() => setFeedbackFor(feedbackFor === d.decision_id ? null : d.decision_id)}
                    >
                      {feedbackFor === d.decision_id ? '收起回填' : '回填真实表现'}
                    </button>
                  )}
                </div>
                {feedbackFor === d.decision_id && !d.feedback_at && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <input
                      className="w-28 rounded-lg border border-gray-300 px-2 py-1 text-xs"
                      placeholder="30天销量"
                      inputMode="numeric"
                      value={sales30d}
                      onChange={(e) => setSales30d(e.target.value)}
                    />
                    <input
                      className="w-24 rounded-lg border border-gray-300 px-2 py-1 text-xs"
                      placeholder="评分(1-5)"
                      inputMode="decimal"
                      value={rating}
                      onChange={(e) => setRating(e.target.value)}
                    />
                    <input
                      className="w-40 rounded-lg border border-gray-300 px-2 py-1 text-xs"
                      placeholder="备注（可选）"
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                    />
                    <button
                      type="button"
                      className="rounded-lg bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-hover"
                      onClick={() => handleFeedback(d.decision_id)}
                    >
                      提交回填
                    </button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {decisions && decisions.length === 0 && !loadError && (
        <p className="mt-3 text-xs text-gray-400">该任务下暂无决策留痕——在下方完成第一次拍板。</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-4">
        <input
          className="w-44 rounded-lg border border-gray-300 px-2 py-1.5 text-xs"
          placeholder="候选标识（如 jd:10001）"
          value={candidateId}
          onChange={(e) => setCandidateId(e.target.value)}
        />
        <div className="flex overflow-hidden rounded-lg border border-gray-300">
          {DECISION_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`px-3 py-1.5 text-xs ${decision === opt.value ? 'bg-accent text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setDecision(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          disabled={submitting}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          onClick={handleDecide}
        >
          {submitting ? '提交中…' : '拍板'}
        </button>
      </div>
      {formMessage && <p className="mt-2 text-xs text-gray-500">{formMessage}</p>}
      {feedbackMessage && <p className="mt-1 text-xs text-gray-500">{feedbackMessage}</p>}
    </section>
  )
}
