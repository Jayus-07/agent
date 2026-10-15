import { getCachedUser } from '@/lib/auth'

function identityScope(): { tenantId: string; userId: string } {
  const user = getCachedUser() as Record<string, unknown> | null
  return {
    tenantId: String(user?.tenantId || user?.tenant_id || 'default'),
    userId: String(user?.userId || user?.user_id || user?.username || 'anonymous'),
  }
}

function associationKey(kind: 'conversation' | 'trip', id: string): string {
  const { tenantId, userId } = identityScope()
  return `travel:v2:${kind}:${encodeURIComponent(tenantId)}:${encodeURIComponent(userId)}:${encodeURIComponent(id)}`
}

/** Trip 工作台的默认会话标识；同一 Trip 在刷新和重新打开时复用。 */
export function defaultTravelV2ConversationId(tripId: string): string {
  return `travel-v2-trip-${tripId}`
}

export function getTravelV2TripAssociation(conversationId: string): string {
  try {
    return typeof window === 'undefined' ? '' : window.localStorage.getItem(associationKey('conversation', conversationId)) || ''
  } catch {
    return ''
  }
}

export function setTravelV2TripAssociation(conversationId: string, tripId: string): void {
  try {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(associationKey('conversation', conversationId), tripId)
    window.localStorage.setItem(associationKey('trip', tripId), conversationId)
  } catch {
    // 会话关联索引仅用于恢复入口；正式行程和写入状态始终以服务端为准。
  }
}

export function travelV2ConversationIdForTrip(tripId: string, candidate?: string): string {
  if (candidate && (candidate === defaultTravelV2ConversationId(tripId)
    || getTravelV2TripAssociation(candidate) === tripId)) return candidate
  try {
    if (typeof window !== 'undefined') {
      const stored = window.localStorage.getItem(associationKey('trip', tripId))
      if (stored && getTravelV2TripAssociation(stored) === tripId) return stored
    }
  } catch {
    // 使用稳定默认标识继续读取同 Trip 会话。
  }
  return defaultTravelV2ConversationId(tripId)
}

export function travelV2WorkspaceHref(tripId: string, conversationId: string): string {
  return `/travel/itineraries/${encodeURIComponent(tripId)}?conversation_id=${encodeURIComponent(conversationId)}`
}

export function travelV2WorkspaceRestoreHref(tripId: string, conversationId: string, revision: number): string {
  return `${travelV2WorkspaceHref(tripId, conversationId)}&restore_revision=${encodeURIComponent(String(revision))}`
}

export function travelV2ChatHref(tripId: string, conversationId: string): string {
  return `/travel/chat?trip_id=${encodeURIComponent(tripId)}&conversation_id=${encodeURIComponent(conversationId)}`
}

/** 只识别带明确正式版本号的恢复指令；提交仍需用户在历史面板确认。 */
export function parseTravelV2RestoreRevisionRequest(message: string): number | null {
  const text = message.trim()
  const restoreIndex = text.search(/恢复|还原|回退|回到|restore|revert/i)
  if (restoreIndex < 0) return null
  const afterRestore = text.slice(restoreIndex)
  const matches = [
    /(?:版本|revision|version)\s*[vV]?\s*(\d+)/i,
    /\bv\s*(\d+)\b/i,
    /第\s*(\d+)\s*(?:个)?(?:版本|版)/,
  ].flatMap((pattern) => {
    const match = pattern.exec(afterRestore)
    return match ? [{ index: match.index, revision: Number(match[1]) }] : []
  }).sort((left, right) => left.index - right.index)
  const revision = matches[0]?.revision
  return Number.isSafeInteger(revision) && revision > 0 ? revision : null
}
