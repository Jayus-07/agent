'use client'

// cs-workstation-v2 P4：快捷回复面板（输入区左下弹层）。
// 搜索（标题/内容/分类）+ 分类过滤 + 点击插入；个人/团队徽章；禁用行置灰。
// 插入语义对齐原型：已有内容时换行追加。
import { useEffect, useMemo, useState } from 'react'
import { listCanned, type CannedItem } from './api'

interface Props {
  open: boolean
  onClose: () => void
  onInsert: (content: string) => void
}

export default function CannedReplyPanel({ open, onClose, onInsert }: Props) {
  const [items, setItems] = useState<CannedItem[]>([])
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('全部')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setQuery('')
      setCategory('全部')
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    listCanned()
      .then((rows) => {
        if (!cancelled) setItems(rows)
      })
      .catch(() => {
        if (!cancelled) setError('话术加载失败，请重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const categories = useMemo(() => {
    const set = new Set<string>()
    for (const item of items) set.add(item.category)
    return ['全部', ...Array.from(set).sort()]
  }, [items])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return items.filter((item) => {
      if (category !== '全部' && item.category !== category) return false
      if (!q) return true
      return (
        item.title.toLowerCase().includes(q)
        || item.content.toLowerCase().includes(q)
        || item.category.toLowerCase().includes(q)
      )
    })
  }, [items, query, category])

  if (!open) return null

  return (
    <div
      className="absolute bottom-[calc(100%+6px)] left-3 z-40 w-[340px] rounded-xl border border-slate-200 bg-white p-2 shadow-xl"
      role="dialog"
      aria-label="快捷回复面板"
    >
      <input
        aria-label="搜索话术"
        placeholder="搜索话术…"
        className="mb-2 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-indigo-400"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      <div className="mb-2 flex flex-wrap gap-1">
        {categories.map((cat) => (
          <button
            key={cat}
            className={
              cat === category
                ? 'rounded-full bg-indigo-600 px-2 py-0.5 text-[11px] text-white'
                : 'rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 hover:bg-slate-200'
            }
            onClick={() => setCategory(cat)}
          >
            {cat}
          </button>
        ))}
      </div>
      <div className="max-h-[220px] overflow-auto">
        {loading && <p className="p-2 text-xs text-slate-400">加载中…</p>}
        {error && <p className="p-2 text-xs text-red-500">{error}</p>}
        {!loading && !error && filtered.length === 0 && (
          <p className="p-2 text-xs text-slate-400">无匹配话术</p>
        )}
        {filtered.map((item) => (
          <button
            key={item.id}
            disabled={!item.enabled}
            className={
              item.enabled
                ? 'mb-1 w-full rounded-lg px-2 py-1.5 text-left hover:bg-indigo-50'
                : 'mb-1 w-full cursor-not-allowed rounded-lg px-2 py-1.5 text-left opacity-40'
            }
            onClick={() => {
              onInsert(item.content)
              onClose()
            }}
          >
            <span className="flex items-center gap-1">
              <b className="text-xs text-slate-800">{item.title}</b>
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                {item.category}
              </span>
              <span
                className={
                  item.scope === 'team'
                    ? 'rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600'
                    : 'rounded-full bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600'
                }
              >
                {item.scope === 'team' ? '团队' : '个人'}
              </span>
              {!item.enabled && (
                <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400">
                  已停用
                </span>
              )}
            </span>
            <span className="mt-0.5 block truncate text-[11px] text-slate-500">
              {item.content}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
