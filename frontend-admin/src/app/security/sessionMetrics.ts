import type { SessionRow } from '@/api/securityOps'

export const RECENT_ACTIVITY_WINDOW_MINUTES = 15
const RECENT_ACTIVITY_WINDOW_MS = RECENT_ACTIVITY_WINDOW_MINUTES * 60 * 1000

/** 统计最近 15 分钟内有活动的有效会话。 */
export function countRecentlyActiveSessions(
  sessions: ReadonlyArray<Pick<SessionRow, 'lastActiveAt'>>,
  now = Date.now(),
): number {
  const cutoff = now - RECENT_ACTIVITY_WINDOW_MS
  return sessions.filter(({ lastActiveAt }) => {
    const activeAt = Date.parse(lastActiveAt)
    return Number.isFinite(activeAt) && activeAt >= cutoff && activeAt <= now
  }).length
}
