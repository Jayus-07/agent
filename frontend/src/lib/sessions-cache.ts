/**
 * sessions-cache — /api/memory/sessions 的内存 dedup + TTL 缓存
 *
 * 用途：
 *  - HistorySidebar mount + tasks page mount 都会请求同一接口；
 *    没有 dedup 时两次请求并发打到后端，PG 全表扫描被放大。
 *  - 用户来回切换时（50ms 内）复用前次结果。
 *  - TTL 10s 防止连续请求打爆后端，又不至于过期（业务变更后最多 10s 看到新数据）。
 */
import { listSessions, type SessionMeta } from './api/memory'

interface CacheEntry {
  promise: Promise<SessionMeta[]>
  cachedAt: number
}

const TTL_MS = 10_000
let cache: CacheEntry | null = null

export async function getSessionsCached(forceRefresh = false): Promise<SessionMeta[]> {
  const now = Date.now()
  if (!forceRefresh && cache && now - cache.cachedAt < TTL_MS) {
    return cache.promise
  }
  const promise = listSessions().catch((err) => {
    // 失败时主动清缓存，下次重新发起；保留当前 entry（如果有）供 stale fallback
    cache = null
    throw err
  })
  cache = { promise, cachedAt: now }
  return promise
}

/** 失效缓存：删除/重命名会话成功后调用，避免 UI 显示陈旧数据 */
export function invalidateSessionsCache(): void {
  cache = null
}
