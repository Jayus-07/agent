'use client'

import { CS_NODE_LABELS } from './constants'

interface Props {
  currentStatus: string
  isLoading: boolean
  intentDetected: string | null
  handoffState: string
}

export default function CSStatusBar({ currentStatus, isLoading, intentDetected, handoffState }: Props) {
  if (!isLoading && !intentDetected && handoffState === 'none') return null

  const nodeLabel = currentStatus ? CS_NODE_LABELS[currentStatus] || currentStatus : ''

  let statusText = ''
  let statusColor = 'text-text-muted'

  if (handoffState === 'waiting') {
    statusText = '等待人工客服接入...'
    statusColor = 'text-orange-500'
  } else if (handoffState === 'active') {
    statusText = '人工客服已接入'
    statusColor = 'text-green-600'
  } else if (isLoading && nodeLabel) {
    statusText = `正在${nodeLabel}...`
    statusColor = 'text-accent'
  } else if (isLoading) {
    statusText = '处理中...'
    statusColor = 'text-accent'
  } else if (intentDetected) {
    statusText = `已识别意图: ${intentDetected}`
    statusColor = 'text-text-secondary'
  }

  if (!statusText) return null

  return (
    // shrink-0：状态栏是抽屉固定底栏之一，避免被内容区挤压变形
    <div className="shrink-0 px-4 py-1.5 border-t border-border-subtle bg-surface-base">
      <div className="flex items-center gap-2">
        {isLoading && (
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" />
        )}
        <span className={`text-[11px] ${statusColor} truncate`}>{statusText}</span>
      </div>
    </div>
  )
}
