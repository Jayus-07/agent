'use client'

import { UserCircle, Clock, CheckCircle, XCircle } from 'lucide-react'
import type { CSHandoffState } from './constants'

interface Props {
  handoffState: CSHandoffState
}

const STATUS_CONFIG: Record<string, { icon: typeof Clock; label: string; color: string; bg: string }> = {
  requested: { icon: Clock, label: '正在请求转接人工客服...', color: 'text-blue-700', bg: 'bg-blue-50 border-blue-200' },
  waiting:   { icon: Clock, label: '等待人工客服接入...', color: 'text-orange-700', bg: 'bg-orange-50 border-orange-200' },
  active:    { icon: UserCircle, label: '人工客服已接入', color: 'text-green-700', bg: 'bg-green-50 border-green-200' },
  closed:    { icon: CheckCircle, label: '人工服务已结束', color: 'text-text-muted', bg: 'bg-gray-50 border-gray-200' },
}

export default function CSHandoffCard({ handoffState }: Props) {
  if (handoffState === 'none') return null
  const config = STATUS_CONFIG[handoffState]
  if (!config) return null

  const Icon = config.icon

  return (
    <div className="flex gap-3 mb-4">
      <div className="flex-1 max-w-[80%] ml-11">
        <div className={`flex items-center gap-2 rounded-xl border px-4 py-3 ${config.bg}`}>
          <Icon size={16} className={config.color} />
          <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
          {handoffState === 'requested' && (
            <span className="ml-auto flex gap-0.5">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" />
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce [animation-delay:0.15s]" />
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce [animation-delay:0.3s]" />
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
