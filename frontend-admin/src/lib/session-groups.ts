/**
 * session-groups — 会话列表的时间分组工具（单一数据源）
 *
 * 用途：HistorySidebar（右侧历史栏，保留回滚）与 TaskSidebar 的会话列表
 * 共用同一套「今天 / 昨天 / 最近 7 天 / 更早」分组逻辑与时间显示格式。
 * 抽出的原因：两处各写一份会把 380 行实现复制粘贴，分组口径容易漂移。
 */
import type { SessionMeta } from '@/api/memory'

export type TimeBucket = 'today' | 'yesterday' | 'week' | 'older'

export const BUCKET_LABELS: Record<TimeBucket, string> = {
  today: '今天',
  yesterday: '昨天',
  week: '最近 7 天',
  older: '更早',
}

export const BUCKET_ORDER: TimeBucket[] = ['today', 'yesterday', 'week', 'older']

/** 按 updated_at 落到时间桶；非法/缺失时间归入「更早」 */
export function bucketOf(iso: string | null | undefined): TimeBucket {
  if (!iso) return 'older'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'older'
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t = d.getTime()
  if (t >= startOfToday) return 'today'
  if (t >= startOfToday - 86_400_000) return 'yesterday'
  if (t >= startOfToday - 7 * 86_400_000) return 'week'
  return 'older'
}

/** 相对时间显示（WorkBuddy 式）：刚刚 / N分钟前 / N小时前 / 更早回退日期 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}天前`
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export interface TimeGroup {
  bucket: TimeBucket
  items: SessionMeta[]
}

/** 按时间桶分组，只返回非空桶，顺序固定为 今天 → 昨天 → 最近 7 天 → 更早 */
export function groupByTime(sessions: SessionMeta[]): TimeGroup[] {
  const map = new Map<TimeBucket, SessionMeta[]>()
  for (const s of sessions) {
    const b = bucketOf(s.updated_at)
    if (!map.has(b)) map.set(b, [])
    map.get(b)!.push(s)
  }
  return BUCKET_ORDER.filter((b) => map.has(b)).map((bucket) => ({ bucket, items: map.get(bucket)! }))
}

/** 标题关键字过滤（大小写不敏感），关键字为空时原样返回 */
export function filterByKeyword(sessions: SessionMeta[], keyword: string): SessionMeta[] {
  const kw = keyword.trim().toLowerCase()
  if (!kw) return sessions
  return sessions.filter((s) => s.title?.toLowerCase().includes(kw))
}
