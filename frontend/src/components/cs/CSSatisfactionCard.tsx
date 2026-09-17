'use client'

/**
 * CSSatisfactionCard — 用户端满意度评分卡（014_cs_rating 需求闭环）
 *
 * 展示时机：当前会话有 AI/坐席回复且未评分、非流式中。
 * 提交：POST /api/cs/conversations/{conversation_id}/rating（1-5 星 + 选填备注）。
 * conversation_id 与后端一致（context.py: conversation_id 默认等于 session_id）。
 */
import { useState } from 'react'
import { Star, Check, X } from 'lucide-react'
import { fetchRaw } from '@/api/client'

interface CSSatisfactionCardProps {
  conversationId: string
  onDismissed?: () => void
}

export default function CSSatisfactionCard({ conversationId, onDismissed }: CSSatisfactionCardProps) {
  const [rating, setRating] = useState(0)
  const [hovered, setHovered] = useState(0)
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (rating < 1 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await fetchRaw(`/api/cs/conversations/${encodeURIComponent(conversationId)}/rating`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, comment: comment.trim() || undefined }),
      })
      setDone(true)
    } catch (e) {
      setError((e as Error).message || '提交失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <div className="mx-3 mb-2 px-3 py-2.5 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center gap-2">
        <Check size={15} className="text-emerald-600 shrink-0" />
        <span className="text-xs text-emerald-700">感谢您的评价，我们会持续改进服务！</span>
      </div>
    )
  }

  return (
    <div
      data-testid="cs-satisfaction-card"
      className="mx-3 mb-2 px-3.5 py-3 rounded-xl bg-surface-raised border border-border-subtle space-y-2"
    >
      {/* 标题行 */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-text-primary flex-1">
          请对本次服务满意度评分：
        </span>
        <button
          onClick={onDismissed}
          title="暂不评价"
          aria-label="暂不评价"
          className="text-text-muted hover:text-text-primary transition-colors"
        >
          <X size={13} />
        </button>
      </div>

      {/* 星标 */}
      <div className="flex items-center gap-1">
        {[1, 2, 3, 4, 5].map((n) => {
          const active = n <= (hovered || rating)
          return (
            <button
              key={n}
              onMouseEnter={() => setHovered(n)}
              onMouseLeave={() => setHovered(0)}
              onClick={() => setRating(n)}
              aria-label={`${n} 星`}
              className="transition-transform hover:scale-110"
            >
              <Star
                size={20}
                className={active ? 'text-amber-400' : 'text-slate-300'}
                fill={active ? 'currentColor' : 'none'}
              />
            </button>
          )
        })}
        {rating > 0 && (
          <span className="ml-1.5 text-[10px] text-text-muted">
            {['', '很不满', '不满', '一般', '满意', '非常满意'][rating]}
          </span>
        )}
      </div>

      {/* 备注 + 提交 */}
      {rating > 0 && (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="补充说明（选填）"
            maxLength={200}
            className="flex-1 min-w-0 text-xs px-2.5 py-1.5 rounded-lg border border-border-subtle bg-white text-text-primary placeholder:text-text-muted outline-none focus:border-accent/50 transition-colors"
          />
          <button
            onClick={submit}
            disabled={submitting}
            className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-accent text-white
              hover:bg-accent/90 disabled:opacity-50 transition-colors"
          >
            {submitting ? '提交中…' : '提交评价'}
          </button>
        </div>
      )}

      {error && <p className="text-[11px] text-red-600">{error}</p>}
    </div>
  )
}
