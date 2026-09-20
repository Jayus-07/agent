'use client'

import type { ClarificationEvent } from '@/lib/types'

interface Props {
  event: ClarificationEvent
  onSelect: (label: string) => void
  onHandoff?: () => void
  disabled?: boolean
}

/** 展示安全的业务选项，不把内部 capability 标识暴露给用户。 */
export default function ClarificationCard({ event, onSelect, onHandoff, disabled = false }: Props) {
  return (
    <section className="mx-5 my-3 rounded-2xl border border-amber-200 bg-amber-50/80 p-4 shadow-sm">
      <p className="text-sm font-medium text-amber-950">{event.question}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {event.options.map((option) => (
          <button
            key={option.id}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(option.label)}
            className="rounded-xl border border-amber-300 bg-white px-3 py-2 text-sm text-amber-900 transition hover:border-amber-500 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {option.label}
          </button>
        ))}
        {event.handoff_available && onHandoff && (
          <button
            type="button"
            disabled={disabled}
            onClick={onHandoff}
            className="rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            转人工
          </button>
        )}
      </div>
    </section>
  )
}
