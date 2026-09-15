/** session-groups 回归测试 — 时间分组口径与关键字过滤（HistorySidebar / TaskSidebar 共用） */
import { describe, it, expect } from 'vitest'
import { bucketOf, formatTime, groupByTime, filterByKeyword, BUCKET_LABELS } from './session-groups'
import type { SessionMeta } from './api/memory'

const HOUR = 3_600_000
const DAY = 86_400_000

function meta(id: string, updatedAt: string | null, title = id): SessionMeta {
  return {
    session_id: id,
    title,
    message_count: 1,
    created_at: updatedAt,
    updated_at: updatedAt,
  }
}

describe('bucketOf — 时间桶归属', () => {
  it('缺失或非法时间归入「更早」', () => {
    expect(bucketOf(null)).toBe('older')
    expect(bucketOf(undefined)).toBe('older')
    expect(bucketOf('not-a-date')).toBe('older')
  })

  it('今天 / 昨天 / 最近 7 天 / 更早 各自落桶', () => {
    const now = Date.now()
    expect(bucketOf(new Date(now).toISOString())).toBe('today')
    expect(bucketOf(new Date(now - 26 * HOUR).toISOString())).toBe('yesterday')
    expect(bucketOf(new Date(now - 3 * DAY).toISOString())).toBe('week')
    expect(bucketOf(new Date(now - 30 * DAY).toISOString())).toBe('older')
  })

  it('每个桶都有中文标签', () => {
    expect(BUCKET_LABELS.today).toBe('今天')
    expect(BUCKET_LABELS.yesterday).toBe('昨天')
    expect(BUCKET_LABELS.week).toBe('最近 7 天')
    expect(BUCKET_LABELS.older).toBe('更早')
  })
})

describe('formatTime — 相对时间显示（WorkBuddy 式）', () => {
  it('1小时内 → 刚刚/N分钟前；24小时内 → N小时前', () => {
    expect(formatTime(new Date(Date.now() - 10_000).toISOString())).toBe('刚刚')
    expect(formatTime(new Date(Date.now() - 5 * 60_000).toISOString())).toBe('5分钟前')
    expect(formatTime(new Date(Date.now() - 3 * 3_600_000).toISOString())).toBe('3小时前')
  })

  it('7天内按天回退，更早显示月/日', () => {
    const now = new Date()
    const days = 2
    expect(formatTime(new Date(now.getTime() - days * DAY).toISOString())).toBe(`${days}天前`)

    const old = new Date(now.getTime() - 30 * DAY)
    expect(formatTime(old.toISOString())).toBe(`${old.getMonth() + 1}/${old.getDate()}`)
  })

  it('空值/非法值返回空串', () => {
    expect(formatTime(null)).toBe('')
    expect(formatTime('nope')).toBe('')
  })
})

describe('groupByTime — 分组顺序与内容', () => {
  it('只返回非空桶，且顺序固定为 今天 → 昨天 → 更早', () => {
    const now = Date.now()
    const groups = groupByTime([
      meta('old', new Date(now - 30 * DAY).toISOString()),
      meta('today', new Date(now).toISOString()),
      meta('yest', new Date(now - 26 * HOUR).toISOString()),
    ])

    expect(groups.map((g) => g.bucket)).toEqual(['today', 'yesterday', 'older'])
    expect(groups[0].items.map((s) => s.session_id)).toEqual(['today'])
    expect(groups[1].items.map((s) => s.session_id)).toEqual(['yest'])
    expect(groups[2].items.map((s) => s.session_id)).toEqual(['old'])
  })

  it('空输入返回空数组', () => {
    expect(groupByTime([])).toEqual([])
  })
})

describe('filterByKeyword — 标题过滤', () => {
  const list = [meta('a', null, '销量前 10 商品'), meta('b', null, 'Refund Policy')]

  it('关键字为空时原样返回', () => {
    expect(filterByKeyword(list, '  ')).toBe(list)
  })

  it('大小写不敏感，且保留原标题', () => {
    expect(filterByKeyword(list, 'refund').map((s) => s.session_id)).toEqual(['b'])
    expect(filterByKeyword(list, '销量').map((s) => s.session_id)).toEqual(['a'])
  })

  it('无匹配返回空数组', () => {
    expect(filterByKeyword(list, 'zzz')).toEqual([])
  })
})
