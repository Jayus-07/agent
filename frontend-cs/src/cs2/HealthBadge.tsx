'use client'

import { useEffect, useState } from 'react'
import { request } from '@/api/client'

type HealthState = 'checking' | 'ok' | 'fail'

// P0 健康自检：新命名空间空页经 BFF 代理探测后端可达性，只读 /api/health。
export default function HealthBadge() {
  const [state, setState] = useState<HealthState>('checking')

  useEffect(() => {
    let cancelled = false
    request<Record<string, unknown>>('/api/health', { timeout: 5000 })
      .then(() => {
        if (!cancelled) setState('ok')
      })
      .catch(() => {
        if (!cancelled) setState('fail')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const label =
    state === 'ok' ? '后端正常' : state === 'fail' ? '后端不可达' : '检测中…'
  const dot =
    state === 'ok'
      ? 'bg-emerald-500'
      : state === 'fail'
        ? 'bg-red-500'
        : 'bg-slate-300'

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
      <span role="status">{label}</span>
    </span>
  )
}
