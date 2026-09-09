'use client'

import { CS_NODE_LABELS, CS_NODE_ICONS } from './constants'

interface Props {
  nodes: string[]
  currentNode?: string | null
}

export default function CSTimeline({ nodes, currentNode }: Props) {
  const unique = Array.from(new Set(nodes))
  if (unique.length === 0) return null

  return (
    <div className="flex items-center gap-1 flex-wrap mt-2">
      {unique.map((node, idx) => {
        const label = CS_NODE_LABELS[node] || node
        const icon = CS_NODE_ICONS[node] || '•'
        const isActive = node === currentNode
        const isLast = idx === unique.length - 1

        return (
          <span key={`${node}-${idx}`} className="flex items-center gap-1">
            <span
              className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium
                ${isActive
                  ? 'bg-accent/15 text-accent animate-pulse'
                  : 'bg-surface-base text-text-muted border border-border-subtle'
                }`}
            >
              <span>{icon}</span>
              <span>{label}</span>
            </span>
            {!isLast && (
              <span className="text-text-muted/40 text-[10px]">→</span>
            )}
          </span>
        )
      })}
    </div>
  )
}
