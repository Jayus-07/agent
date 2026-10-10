/**
 * sessions-cache — /api/memory/sessions 的内存 dedup + TTL 缓存
 *
 * 用途：
 *  - HistorySidebar mount + tasks page mount 都会请求同一接口；
 *    没有 dedup 时两次请求并发打到后端，PG 全表扫描被放大。
 *  - 用户来回切换时（50ms 内）复用前次结果。
 *  - TTL 10s 防止连续请求打爆后端，又不至于过期（业务变更后最多 10s 看到新数据）。
 */
import {
  listSessions,
  listSessionsPage,
  SESSION_PAGE_SIZE,
  type SessionMeta,
  type SessionPage,
} from '@/api/memory'

interface CacheEntry<T> {
  promise: Promise<T>
  cachedAt: number
}

const TTL_MS = 10_000
let cache: CacheEntry<SessionMeta[]> | null = null
let pageCache: CacheEntry<SessionPage> | null = null

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

/** 历史会话首屏分页缓存，与旧版完整列表缓存分开，避免影响其他调用方。 */
export async function getSessionPageCached(forceRefresh = false): Promise<SessionPage> {
  const now = Date.now()
  if (!forceRefresh && pageCache && now - pageCache.cachedAt < TTL_MS) {
    return pageCache.promise
  }
  const promise = listSessionsPage(SESSION_PAGE_SIZE).catch((err) => {
    pageCache = null
    throw err
  })
  pageCache = { promise, cachedAt: now }
  return promise
}

/** 失效缓存：删除/重命名会话成功后调用，避免 UI 显示陈旧数据 */
export function invalidateSessionsCache(): void {
  cache = null
  pageCache = null
}
