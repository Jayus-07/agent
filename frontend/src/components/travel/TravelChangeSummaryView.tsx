'use client'

import { useState } from 'react'
import type { ItineraryChange, ItineraryChangeKind, TravelChangeSummary } from './travelRuntime'

const CHANGE_LABEL: Record<ItineraryChangeKind, string> = {
  added: '新增',
  removed: '删除',
  replaced: '可能替换',
  moved: '移动',
  time_changed: '时段调整',
}

const CHANGE_STYLE: Record<ItineraryChangeKind, string> = {
  added: 'border-[#c7e5dc] bg-[#f2faf7] text-[#26765f]',
  removed: 'border-[#ead6d8] bg-[#fbf6f6] text-[#9a5961]',
  replaced: 'border-[#ead9b8] bg-[#fffaf0] text-[#8c5a10]',
  moved: 'border-[#d8e1e8] bg-[#f5f8fa] text-[#536c7b]',
  time_changed: 'border-[#d8e1e8] bg-[#f5f8fa] text-[#536c7b]',
}

function itemName(change: ItineraryChange): string {
  return change.nextItem?.poi?.name || change.nextItem?.title
    || change.previousItem?.poi?.name || change.previousItem?.title || '行程项目'
}

function scheduledTime(item: ItineraryChange['previousItem']): string | undefined {
  if (!item) return undefined
  if (item.start && item.end) return `${item.start}–${item.end}`
  return item.start || item.end || undefined
}

function changeText(change: ItineraryChange): string {
  const previous = change.previousItem?.poi?.name || change.previousItem?.title
  const next = change.nextItem?.poi?.name || change.nextItem?.title
  const previousTime = change.details.previousTime || scheduledTime(change.previousItem)
  const nextTime = change.details.nextTime || scheduledTime(change.nextItem)
  if (change.kind === 'replaced') {
    return `${previous || '原项目'} → ${next || '新项目'}${previousTime && nextTime ? ` · ${previousTime} → ${nextTime}` : ''}`
  }
  if (change.kind === 'moved') {
    return `${itemName(change)}${change.details.timeChanged && previousTime && nextTime
      ? ` · 同时调整时段 ${previousTime} → ${nextTime}` : ''}`
  }
  if (change.kind === 'time_changed') {
    return `${itemName(change)}${previousTime && nextTime ? ` · ${previousTime} → ${nextTime}` : ''}`
  }
  const time = change.nextItem ? nextTime : previousTime
  return `${itemName(change)}${time ? ` · ${time}` : ''}`
}

function dayLabel(change: ItineraryChange): string {
  if (change.kind === 'moved') return `第 ${change.previousDay} 天 → 第 ${change.nextDay} 天`
  const day = change.nextDay ?? change.previousDay
  return day == null ? '行程调整' : `第 ${day} 天`
}

export function changeCounts(summary: TravelChangeSummary): Record<ItineraryChangeKind, number> {
  return {
    added: summary.itemChanges.filter((change) => change.kind === 'added').length,
    removed: summary.itemChanges.filter((change) => change.kind === 'removed').length,
    replaced: summary.itemChanges.filter((change) => change.kind === 'replaced').length,
    moved: summary.itemChanges.filter((change) => change.kind === 'moved').length,
    time_changed: summary.itemChanges.filter((change) => change.kind === 'time_changed').length,
  }
}

export default function TravelChangeSummaryView({
  summary,
  compact = false,
  expanded: expandedProp,
  onToggle,
}: {
  summary: TravelChangeSummary
  compact?: boolean
  expanded?: boolean
  onToggle?: () => void
}) {
  const [innerExpanded, setInnerExpanded] = useState(false)
  const controlled = expandedProp !== undefined
  const expanded = controlled ? expandedProp : innerExpanded
  const counts = changeCounts(summary)
  const total = summary.itemChanges.length
  const limit = compact && !expanded ? 3 : total
  const visibleChanges = summary.itemChanges.slice(0, limit)
  const fieldSummary = summary.briefFields.length > 0 ? summary.briefFields.join('、') : ''
  const hasMore = compact && (total > 3 || Boolean(fieldSummary))

  return (
    <div className="space-y-2" aria-label="行程变化明细">
      <div className="flex flex-wrap gap-1.5" aria-label="变化数量">
        {(Object.keys(CHANGE_LABEL) as ItineraryChangeKind[]).map((kind) => (
          counts[kind] > 0 && (
            <span key={kind} className={`rounded-full border px-2 py-0.5 text-[10px] ${CHANGE_STYLE[kind]}`}>
              {CHANGE_LABEL[kind]} {counts[kind]}
            </span>
          )
        ))}
        {summary.budgetDelta !== 0 && (
          <span className="rounded-full border border-[#dae7e5] bg-white px-2 py-0.5 text-[10px] text-[#5c7074]">
            预算 {summary.budgetDelta > 0 ? '+' : '−'}¥{Math.abs(summary.budgetDelta).toLocaleString()}
          </span>
        )}
        {fieldSummary && (
          <span className="rounded-full border border-[#dae7e5] bg-white px-2 py-0.5 text-[10px] text-[#5c7074]">
            需求调整
          </span>
        )}
      </div>

      {fieldSummary && (!compact || expanded) && (
        <p className="text-[11px] leading-relaxed text-[#5c7074]">
          需求字段：{fieldSummary}
        </p>
      )}

      {visibleChanges.length > 0 ? (
        <ul className="space-y-1.5">
          {visibleChanges.map((change, index) => (
            <li
              key={`${change.kind}-${change.previousDay ?? ''}-${change.nextDay ?? ''}-${itemName(change)}-${index}`}
              className="rounded-lg border border-[#e5ecea] bg-white px-2.5 py-2"
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[10px] text-[#8aa09c]">{dayLabel(change)}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${CHANGE_STYLE[change.kind]}`}>
                  {change.kind === 'replaced' && change.certainty === 'inferred'
                    ? '可能替换' : CHANGE_LABEL[change.kind]}
                </span>
              </div>
              <p className="mt-1 break-words text-[11px] leading-relaxed text-[#344b50]">{changeText(change)}</p>
            </li>
          ))}
        </ul>
      ) : total === 0 && !fieldSummary && summary.budgetDelta === 0 ? (
        <p className="text-[11px] text-[#718780]">未发现可量化的行程差异。</p>
      ) : null}

      {hasMore && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => onToggle ? onToggle() : setInnerExpanded((value) => !value)}
          className="text-[10px] font-medium text-[#087b73] hover:underline"
        >
          {expanded
            ? '收起变化'
            : total > 3 ? `查看全部 ${total} 条变化` : '查看需求字段'}
        </button>
      )}
    </div>
  )
}
