'use client'

import { useEffect, useRef, useState } from 'react'
import { Clock3, RotateCcw, X } from 'lucide-react'
import {
  fetchTravelTripRevisionsV2,
  type TravelTripRevisionV2,
} from '@/api/travelV2'
import {
  travelWriteStatusRole,
  travelWriteStatusTone,
  type TravelWriteStatus,
} from './travelV2WriteState'
import TravelTripRevisionActions from './TravelTripRevisionActions'

interface TravelTripHistoryPanelProps {
  tripId: string
  currentRevision: number
  restoreRequest?: { revision: number } | null
  restoreStatus: TravelWriteStatus | null
  restoreMessage: string
  writeBlockedMessage?: string
  canRetryRestore: boolean
  onClose: () => void
  onRestore: (targetRevision: number) => void
  onRetryRestore: () => void
  onRefreshLatest: () => void
}

export default function TravelTripHistoryPanel({
  tripId,
  currentRevision,
  restoreRequest = null,
  restoreStatus,
  restoreMessage,
  writeBlockedMessage = '',
  canRetryRestore,
  onClose,
  onRestore,
  onRetryRestore,
  onRefreshLatest,
}: TravelTripHistoryPanelProps) {
  const [revisions, setRevisions] = useState<TravelTripRevisionV2[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [restoreTarget, setRestoreTarget] = useState<number | null>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setLoadError('')
    void fetchTravelTripRevisionsV2(tripId, { signal: controller.signal })
      .then((response) => {
        if (!controller.signal.aborted) {
          if (response.trip_id !== tripId) throw new Error('服务器返回了不匹配的行程历史。')
          setRevisions(response.revisions
            .filter((entry) => entry.trip_id === tripId)
            .sort((left, right) => right.revision - left.revision))
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setLoadError(cause instanceof Error ? cause.message : '版本历史暂时无法读取。')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [tripId, currentRevision])

  useEffect(() => {
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  useEffect(() => {
    if (restoreStatus === 'SAVED') setRestoreTarget(null)
  }, [restoreStatus, currentRevision])

  useEffect(() => {
    if (!restoreRequest || loading || loadError) return
    const target = revisions.find((entry) => entry.revision === restoreRequest.revision)
    setRestoreTarget(target && target.revision !== currentRevision ? target.revision : null)
  }, [currentRevision, loadError, loading, restoreRequest, revisions])

  return <div
    className="fixed inset-0 z-[1000] bg-[#17283f]/35 lg:flex lg:justify-end"
    role="presentation"
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
  >
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="travel-trip-history-title"
      className="absolute inset-x-0 bottom-0 flex max-h-[88dvh] flex-col rounded-t-[24px] border border-[#e4ebf4] bg-white pb-[calc(1rem+env(safe-area-inset-bottom))] shadow-[0_-12px_48px_rgba(15,36,62,.18)] lg:static lg:h-full lg:max-h-none lg:w-[440px] lg:rounded-none lg:rounded-l-[24px] lg:pb-0 lg:shadow-[-12px_0_48px_rgba(15,36,62,.16)]"
    >
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-[#e8edf4] px-5 py-4 lg:px-6">
        <div>
          <h2 id="travel-trip-history-title" className="text-base font-semibold text-[#263b55]">版本历史</h2>
          <p className="mt-1 text-xs leading-5 text-[#71839b]">恢复会在当前版本后新增 Revision，已有历史会保留。</p>
        </div>
        <button
          ref={closeRef}
          type="button"
          aria-label="关闭版本历史"
          onClick={onClose}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[#637891] hover:bg-[#f2f6fb] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#2878f5]"
        ><X size={18} aria-hidden /></button>
      </header>

      {(restoreMessage || restoreStatus) && <div className="shrink-0 px-5 pt-4 lg:px-6">
        <div
          role={restoreStatus ? travelWriteStatusRole(restoreStatus) : 'status'}
          className={`rounded-xl border px-3.5 py-3 text-sm leading-5 ${restoreStatus ? travelWriteStatusTone(restoreStatus) : 'border-[#cfe0f5] bg-[#eff6ff] text-[#315f91]'}`}
        >
          <p>{restoreMessage}</p>
          {restoreStatus === 'UNKNOWN' && canRetryRestore && <button
            type="button"
            onClick={onRetryRestore}
            className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950"
          >使用同一请求安全重试</button>}
          {restoreStatus === 'CONFLICT' && <button
            type="button"
            onClick={onRefreshLatest}
            className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950"
          >刷新最新版本并重新编辑</button>}
        </div>
      </div>}

      {restoreRequest && !loading && !loadError && restoreRequest.revision === currentRevision && <p role="status" className="mx-5 mt-4 rounded-xl border border-[#cfe0f5] bg-[#eff6ff] px-3.5 py-3 text-sm leading-5 text-[#315f91] lg:mx-6">v{restoreRequest.revision} 已是当前正式版本，无需恢复。</p>}
      {restoreRequest && !loading && !loadError && !revisions.some((entry) => entry.revision === restoreRequest.revision) && <p role="alert" className="mx-5 mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm leading-5 text-amber-950 lg:mx-6">找不到正式版本 v{restoreRequest.revision}。请从下方选择已有版本。</p>}

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4 lg:px-6">
        {writeBlockedMessage && <p role="alert" className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm leading-5 text-amber-950">{writeBlockedMessage}</p>}
        {loading && <p role="status" className="py-8 text-center text-sm text-[#71839b]">正在读取版本历史…</p>}
        {!loading && loadError && <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm leading-5 text-rose-800" role="alert">版本历史暂不可用：{loadError}</div>}
        {!loading && !loadError && revisions.length === 0 && <p className="rounded-xl border border-dashed border-[#d8e2ef] px-4 py-8 text-center text-sm text-[#71839b]">暂时没有可显示的版本。</p>}
        {!loading && !loadError && revisions.length > 0 && <ol className="relative space-y-0 before:absolute before:bottom-5 before:left-[13px] before:top-5 before:w-px before:bg-[#dce6f2]">
          {revisions.map((entry) => {
            const isCurrent = entry.revision === currentRevision
            const isConfirming = restoreTarget === entry.revision
            return <li key={`${entry.trip_id}:${entry.revision}`} className="relative pb-5 pl-10 last:pb-0">
              <span className={`absolute left-0 top-1 flex h-[27px] w-[27px] items-center justify-center rounded-full border-2 bg-white text-[10px] font-bold ${isCurrent ? 'border-[#2878f5] text-[#2878f5]' : 'border-[#c9d8e9] text-[#6d829b]'}`} aria-hidden>{entry.revision}</span>
              <article className={`rounded-xl border p-3.5 ${isCurrent ? 'border-[#a9c9f2] bg-[#f6faff]' : 'border-[#e8edf4] bg-white'}`}>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-[#2d435e]">版本 v{entry.revision}{isCurrent && <span className="ml-2 rounded-full bg-[#e5f0ff] px-2 py-0.5 text-[11px] font-medium text-[#246bd2]">当前</span>}</p>
                  <span className="inline-flex shrink-0 items-center gap-1 text-xs text-[#71839b]"><Clock3 size={13} aria-hidden />{formatRevisionTime(entry.created_at)}</span>
                </div>
                <p className="mt-2 text-sm leading-5 text-[#536a85]">{entry.change_summary || '行程内容更新'}</p>
                {entry.parent_revision !== null && <p className="mt-1 text-xs text-[#8a99ab]">基于 v{entry.parent_revision}</p>}
                {!isCurrent && !isConfirming && <button
                  type="button"
                  disabled={Boolean(writeBlockedMessage) || restoreStatus === 'VALIDATING' || restoreStatus === 'SAVING' || restoreStatus === 'UNKNOWN'}
                  onClick={() => setRestoreTarget(entry.revision)}
                  className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-lg border border-[#d5e2f1] bg-white px-3 text-sm font-medium text-[#496783] hover:border-[#8db6f8] hover:text-[#246bd2] disabled:cursor-not-allowed disabled:opacity-50"
                ><RotateCcw size={14} aria-hidden />恢复此版本</button>}
                {isConfirming && <div className="mt-3 rounded-lg bg-[#f1f6fc] p-3">
                  <p className="text-sm leading-5 text-[#435b77]">确认将内容恢复到 v{entry.revision}？服务端会创建新的 v{currentRevision + 1}。</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={Boolean(writeBlockedMessage) || restoreStatus === 'VALIDATING' || restoreStatus === 'SAVING' || restoreStatus === 'UNKNOWN'}
                      onClick={() => onRestore(entry.revision)}
                      className="min-h-10 rounded-lg bg-[#2878f5] px-3.5 text-sm font-semibold text-white disabled:opacity-50"
                    >{restoreStatus === 'SAVING' ? '正在恢复…' : `确认恢复 v${entry.revision}`}</button>
                    <button type="button" disabled={restoreStatus === 'UNKNOWN'} onClick={() => setRestoreTarget(null)} className="min-h-10 rounded-lg px-3 text-sm font-medium text-[#637891] hover:bg-white disabled:cursor-not-allowed disabled:opacity-50">取消</button>
                  </div>
                </div>}
                <TravelTripRevisionActions
                  tripId={tripId}
                  revision={entry.revision}
                  writeBlockedMessage={writeBlockedMessage}
                />
              </article>
            </li>
          })}
        </ol>}
      </div>
    </section>
  </div>
}

function formatRevisionTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}
