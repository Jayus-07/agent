import { Check, ChevronLeft, ChevronRight } from 'lucide-react'
import { PROMPT_STATUSES, STATUS_META, type PromptStatus } from '@/services/prompts'

interface StatusPipelineProps {
  current: PromptStatus | string | null
  onTransition?: (target: PromptStatus) => void
  loading?: boolean
}

function canAdvance(from: string | null, to: string): boolean {
  if (!from) return false
  const fi = PROMPT_STATUSES.indexOf(from as PromptStatus)
  const ti = PROMPT_STATUSES.indexOf(to as PromptStatus)
  if (fi < 0 || ti < 0) return false
  const diff = ti - fi
  if (diff === 1) return true
  if (diff === -1 && fi >= 1 && fi <= 3) return true
  return false
}

export default function StatusPipeline({ current, onTransition, loading }: StatusPipelineProps) {
  const ci = current ? PROMPT_STATUSES.indexOf(current as PromptStatus) : -1

  return (
    <div className="flex items-center gap-1">
      {PROMPT_STATUSES.map((s, i) => {
        const meta = STATUS_META[s]
        const active = i === ci
        const done = i < ci
        const canNext = onTransition && canAdvance(current, s)

        return (
          <div key={s} className="flex items-center gap-1">
            {i > 0 && (
              <div className={`w-6 h-px ${done ? 'bg-green-300' : 'bg-border-subtle'}`} />
            )}
            <button
              disabled={!canNext || loading}
              onClick={() => canNext && onTransition?.(s)}
              className={`
                relative flex items-center gap-1 text-[10px] px-2 py-1 rounded-full transition-all
                ${active ? `${meta.bg} ${meta.color} font-semibold ring-1 ring-current/20` : ''}
                ${done ? 'bg-green-50 text-green-600' : ''}
                ${!active && !done ? 'bg-gray-50 text-gray-400' : ''}
                ${canNext ? 'cursor-pointer hover:ring-1 hover:ring-current/30' : 'cursor-default'}
                ${loading ? 'opacity-50' : ''}
              `}
            >
              {done && <Check size={10} />}
              {meta.label}
            </button>
          </div>
        )
      })}

      {current && ci >= 0 && (
        <div className="flex items-center gap-0.5 ml-2">
          {ci < PROMPT_STATUSES.length - 1 && ci >= 0 && PROMPT_STATUSES[ci] !== 'published' && PROMPT_STATUSES[ci] !== 'archived' && (
            <button
              disabled={loading}
              onClick={() => onTransition?.(PROMPT_STATUSES[ci + 1])}
              className="p-1 rounded hover:bg-accent/10 text-accent disabled:opacity-40"
              title="推进到下一阶段"
            >
              <ChevronRight size={14} />
            </button>
          )}
          {ci > 0 && ci >= 1 && ci <= 3 && (
            <button
              disabled={loading}
              onClick={() => onTransition?.(PROMPT_STATUSES[ci - 1])}
              className="p-1 rounded hover:bg-gray-100 text-gray-500 disabled:opacity-40"
              title="回退到上一阶段"
            >
              <ChevronLeft size={14} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}
