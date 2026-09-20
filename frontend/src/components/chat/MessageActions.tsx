'use client'

import { useState, useCallback } from 'react'
import { Copy, Check, RefreshCw, ThumbsUp, ThumbsDown, Pencil, Send } from 'lucide-react'
import { feedbackService } from '@/api/feedback'

interface Props {
  content: string; isUser: boolean; isLast: boolean
  sessionId?: string
  msgId?: string
  traceId?: string
  question?: string
  budgetBlocked?: boolean
  onRegenerate?: () => void; onEdit?: (text: string) => void; onResend?: (text: string) => void
}

export default function MessageActions({
  content, isUser, isLast, sessionId, msgId, traceId, question,
  budgetBlocked = false, onRegenerate, onEdit, onResend,
}: Props) {
  const [copied, setCopied] = useState(false)
  const [liked, setLiked] = useState<'up' | 'down' | null>(null)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(content)
  const [negativeOpen, setNegativeOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [correctionText, setCorrectionText] = useState('')
  const [expectedAnswer, setExpectedAnswer] = useState('')
  const [feedbackError, setFeedbackError] = useState('')
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)

  const handleCopy = useCallback(async () => {
    try { await navigator.clipboard.writeText(content) } catch { /* fallback */ }
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }, [content])

  const submitFeedback = useCallback(async (vote: 'positive' | 'negative', fields: { reason?: string; correction_text?: string; expected_answer?: string } = {}) => {
    if (budgetBlocked) return
    // 切换：再次点击取消
    if (liked === (vote === 'positive' ? 'up' : 'down')) {
      setLiked(null)
      return
    }
    setFeedbackSubmitting(true)
    setFeedbackError('')
    try {
      await feedbackService.send({
        session_id: sessionId || 'unknown',
        vote,
        msg_id: msgId || '',
        trace_id: traceId,
        question: question || '',
        answer_preview: content.slice(0, 200),
        ...fields,
      })
      setLiked(vote === 'positive' ? 'up' : 'down')
      setNegativeOpen(false)
    } catch {
      setFeedbackError('反馈提交失败，请稍后重试')
    } finally {
      setFeedbackSubmitting(false)
    }
  }, [budgetBlocked, liked, sessionId, msgId, question, content])

  const handleFeedback = useCallback((vote: 'positive' | 'negative') => {
    if (budgetBlocked) return
    if (vote === 'negative') {
      if (liked === 'down') { setLiked(null); return }
      setNegativeOpen(true)
      return
    }
    void submitFeedback('positive')
  }, [budgetBlocked, liked, submitFeedback])

  const handleNegativeSubmit = useCallback(() => {
    if (budgetBlocked) return
    if (!reason.trim() && !correctionText.trim() && !expectedAnswer.trim()) {
      setFeedbackError('请至少填写一个原因或改进建议')
      return
    }
    void submitFeedback('negative', {
      reason,
      correction_text: correctionText,
      expected_answer: expectedAnswer,
    })
  }, [budgetBlocked, reason, correctionText, expectedAnswer, submitFeedback])

  if (editing) {
    return (
      <div className="flex items-start gap-2 mt-1">
        <textarea value={editText} onChange={e => setEditText(e.target.value)}
          className="flex-1 bg-surface-base border border-border-subtle rounded-lg px-3 py-1.5 text-xs text-text-primary resize-none outline-none focus:border-accent/40"
          rows={2} />
        <button onClick={() => { onEdit?.(editText); setEditing(false) }}
          disabled={budgetBlocked}
          className="shrink-0 p-1.5 rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">
          <Send size={13} />
        </button>
        <button onClick={() => setEditing(false)}
          className="shrink-0 p-1.5 rounded-lg border border-border-subtle text-text-muted hover:text-text-secondary transition-colors text-[10px]">
          取消
        </button>
      </div>
    )
  }

  return (
    <>
    {negativeOpen && !isUser && !budgetBlocked && (
      <div className="mt-2 rounded-xl border border-red-100 bg-red-50/60 p-3 text-xs">
        <p className="font-medium text-red-900">告诉我们哪里需要改进</p>
        <select value={reason} onChange={(event) => setReason(event.target.value)} className="mt-2 w-full rounded-lg border border-red-100 bg-white px-2 py-1.5 text-xs text-slate-700">
          <option value="">选择原因（可选）</option>
          <option value="不准确">内容不准确</option>
          <option value="不完整">内容不完整</option>
          <option value="没理解问题">没有理解问题</option>
          <option value="格式不合适">格式不合适</option>
        </select>
        <textarea value={correctionText} onChange={(event) => setCorrectionText(event.target.value)} placeholder="可以直接写出纠正内容（可选）" rows={2} className="mt-2 w-full resize-none rounded-lg border border-red-100 bg-white px-2 py-1.5 text-xs outline-none focus:border-red-300" />
        <textarea value={expectedAnswer} onChange={(event) => setExpectedAnswer(event.target.value)} placeholder="你期待的答案（可选）" rows={2} className="mt-2 w-full resize-none rounded-lg border border-red-100 bg-white px-2 py-1.5 text-xs outline-none focus:border-red-300" />
        {feedbackError && <p className="mt-1 text-red-700">{feedbackError}</p>}
        <div className="mt-2 flex justify-end gap-2">
          <button type="button" onClick={() => { setNegativeOpen(false); setFeedbackError('') }} className="rounded-lg px-2.5 py-1 text-xs text-slate-500 hover:bg-white">取消</button>
          <button type="button" disabled={feedbackSubmitting} onClick={handleNegativeSubmit} className="rounded-lg bg-red-600 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50">{feedbackSubmitting ? '提交中…' : '提交反馈'}</button>
        </div>
      </div>
    )}
    <div className="flex items-center justify-end gap-0.5 mt-1.5 opacity-0 group-hover/message:opacity-100 focus-within:opacity-100 transition-opacity duration-200">
      <ActionBtn icon={copied ? <Check size={12} className="text-green-500" /> : <Copy size={12} />}
        label={copied ? '已复制' : '复制'} onClick={handleCopy} />
      {isUser && isLast && onEdit && (
        <ActionBtn disabled={budgetBlocked} icon={<Pencil size={12} />} label="编辑" onClick={() => { setEditText(content); setEditing(true) }} />
      )}
      {isUser && isLast && onResend && (
        <ActionBtn disabled={budgetBlocked} icon={<Send size={12} />} label="重发" onClick={() => onResend(content)} />
      )}
      {!isUser && (
        <>
          {onRegenerate && (
            <ActionBtn disabled={budgetBlocked} icon={<RefreshCw size={12} />} label="重新生成" onClick={() => onRegenerate()} />
          )}
          <ActionBtn disabled={budgetBlocked} icon={<ThumbsUp size={12} className={liked === 'up' ? 'text-green-500' : ''} />}
            label="有用" onClick={() => handleFeedback('positive')} />
          <ActionBtn disabled={budgetBlocked} icon={<ThumbsDown size={12} className={liked === 'down' ? 'text-red-500' : ''} />}
            label="无用" onClick={() => handleFeedback('negative')} />
        </>
      )}
    </div>
    </>
  )
}

function ActionBtn({ icon, label, onClick, disabled = false }: { icon: React.ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] text-text-muted hover:text-text-secondary hover:bg-black/5 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      title={label}>
      {icon}{label}
    </button>
  )
}
