'use client'

import { AlertCircle, Check, CircleHelp, LoaderCircle } from 'lucide-react'
import type { TravelSearchEvent } from './TravelSearchPanel'

const labels: Record<TravelSearchEvent['status'], string> = {
  querying: '查询中',
  cancelled: '已取消',
  success: '查询成功',
  no_results: '无结果',
  business_failure: '查询失败',
  network_timeout: '网络超时',
  provider_unavailable: '数据源不可用',
  disabled: '能力未启用',
  request_error: '请求未完成',
}

export default function TravelSearchActivity({ events }: { events: TravelSearchEvent[] }) {
  if (!events.length) {
    return <div className="rounded-xl border border-dashed border-[#dfe8f3] px-3 py-4 text-center text-xs leading-5 text-[#667b94]">
      查询工具运行状态会显示在这里。普通查询不会修改正式行程。
    </div>
  }

  return <ol aria-label="旅游查询运行记录" aria-live="polite" className="space-y-2">
    {events.map((event, index) => {
      const querying = event.status === 'querying'
      const success = event.status === 'success'
      const cancelled = event.status === 'cancelled'
      const Icon = querying ? LoaderCircle : success ? Check : (cancelled || event.status === 'no_results') ? CircleHelp : AlertCircle
      const tone = querying
        ? 'border-[#dbe9fc] bg-[#f4f8ff] text-[#456b9f]'
        : success
          ? 'border-[#d7eee2] bg-[#f4fbf7] text-[#39815d]'
          : cancelled || event.status === 'no_results'
            ? 'border-[#e5eaf1] bg-[#f8fafc] text-[#73849a]'
            : 'border-[#f3ded9] bg-[#fff8f6] text-[#a55f50]'

      const evidence = [
        event.source?.trim() ? event.source : null,
        event.queriedAt ? formatTime(event.queriedAt) : null,
      ].filter(Boolean).join(' · ')

      return <li key={event.queryId} className={`rounded-xl border px-3 py-2.5 ${tone}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5 text-xs font-semibold">
            <Icon size={12} className={querying ? 'animate-spin' : ''} aria-hidden />
            <span className="truncate">{event.message}</span>
          </span>
          <span className="shrink-0 rounded-full bg-white/75 px-2 py-0.5 text-xs">{labels[event.status] || '状态未知'}</span>
        </div>
        {evidence && <p className="mt-1 pl-[18px] text-xs leading-5 opacity-90">{evidence}</p>}
      </li>
    })}
  </ol>
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '时间未知' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}
