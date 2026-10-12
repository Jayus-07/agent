import { ApiError } from '@/api/client'
import type { TravelTripV2, TripDocumentV2 } from '@/api/travelV2'

export type TravelWriteStatus =
  | 'VALIDATING'
  | 'SAVING'
  | 'SAVED'
  | 'INVALID'
  | 'CONFLICT'
  | 'FAILED'
  | 'UNKNOWN'

export interface ConfirmedTravelWrite {
  saved: true
  trip_id: string
  revision: number
  document: TripDocumentV2
}

export function classifyTravelWriteError(cause: unknown): TravelWriteStatus {
  if (!(cause instanceof ApiError)) return 'UNKNOWN'
  if (cause.status === 409 && cause.code === 'VERSION_CONFLICT') return 'CONFLICT'
  if (cause.status === 422) return 'INVALID'
  if (cause.status >= 400 && cause.status < 500 && cause.status !== 408) return 'FAILED'
  return 'UNKNOWN'
}

export function travelWriteErrorMessage(cause: unknown, status: TravelWriteStatus): string {
  const detail = cause instanceof Error ? cause.message : ''
  if (status === 'INVALID') return `服务端校验未通过，正式行程没有更新。${detail ? ` ${detail}` : ''}`
  if (status === 'CONFLICT') return '行程 Revision 已变化，本次操作没有应用。当前页面保留原版本；刷新最新版本后请重新编辑。'
  if (status === 'FAILED') return `服务器明确拒绝了本次操作，正式 Revision 没有更新。${detail ? ` ${detail}` : ''}`
  return '提交结果尚未确认，服务端可能已经完成操作。页面保留上次确认的 Revision；请使用同一请求安全重试。'
}

export function isConfirmedTravelWrite(
  value: unknown,
  tripId: string,
  expectedRevision: number,
): value is ConfirmedTravelWrite {
  if (!value || typeof value !== 'object') return false
  const result = value as Partial<ConfirmedTravelWrite>
  return result.saved === true
    && result.trip_id === tripId
    && Number.isInteger(result.revision)
    && result.revision === expectedRevision + 1
    && Boolean(result.document && result.document.schema_version === 2
      && Array.isArray(result.document.days))
}

export function isConfirmedCreatedTravelTrip(value: unknown): value is TravelTripV2 {
  if (!value || typeof value !== 'object') return false
  const trip = value as Partial<TravelTripV2>
  return typeof trip.trip_id === 'string'
    && trip.trip_id.length > 0
    && trip.status === 'active'
    && trip.revision === 1
    && Boolean(trip.document && trip.document.schema_version === 2
      && Array.isArray(trip.document.days))
}

export function travelWriteStatusLabel(status: TravelWriteStatus): string {
  return {
    VALIDATING: '校验中',
    SAVING: '保存中',
    SAVED: '已保存',
    INVALID: '校验失败',
    CONFLICT: '版本冲突',
    FAILED: '保存失败',
    UNKNOWN: '状态待核实',
  }[status]
}

export function travelWriteStatusRole(status: TravelWriteStatus): 'status' | 'alert' {
  return status === 'INVALID' || status === 'CONFLICT' || status === 'FAILED' || status === 'UNKNOWN'
    ? 'alert'
    : 'status'
}

export function travelWriteStatusTone(status: TravelWriteStatus): string {
  if (status === 'SAVED') return 'border-emerald-200 bg-emerald-50 text-emerald-950'
  if (status === 'INVALID' || status === 'CONFLICT' || status === 'FAILED' || status === 'UNKNOWN') {
    return 'border-amber-200 bg-amber-50 text-amber-950'
  }
  return 'border-[#cfe0f5] bg-[#eff6ff] text-[#315f91]'
}

export function nextPaint(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => resolve())
    else setTimeout(resolve, 0)
  })
}
